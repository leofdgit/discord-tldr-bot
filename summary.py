from dataclasses import dataclass
from discord import Intents, Client, utils
from openai import AsyncOpenAI
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import List
import asyncio
from itertools import groupby
import tiktoken

# Due to uncertainty around the way that OpenAI tokenizes text server-side, include a pessemistic buffer.
OPENAI_TOKEN_BUFFER = 100

# TODO: add more
MODEL_TO_MAX_TOKENS = {
    "gpt-3.5-turbo": 4096,
    "gpt-3.5-turbo-1106": 16385,
    "gpt-4": 8192,
    "gpt-4-1106-preview": 128000,
}


def max_input_tokens(model: str, max_output_tokens: int) -> int:
    max_tokens_for_model = MODEL_TO_MAX_TOKENS[model]
    if not max_tokens_for_model:
        raise Exception(f"Unsupported model: {model}")
    res = max_tokens_for_model - max_output_tokens - OPENAI_TOKEN_BUFFER
    # Arbitrary, but avoid max_output_tokens being far too high for the choice of model
    min_input_tokens = 1000
    if res < min_input_tokens:
        raise Exception(
            f"Too few tokens allocated for input. max_tokens_for_model={max_tokens_for_model}, max_output_tokens={max_output_tokens}, input_tokens={res}. max_tokens_for_model - max_output_tokens must be greater than {min_input_tokens}"
        )
    return res


if ENV_FILE := os.getenv("ENV_FILE"):
    load_dotenv(ENV_FILE)

MAX_OUTPUT_TOKENS = int(os.getenv("MAX_TOKENS", "200"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
MAX_INPUT_TOKENS = max_input_tokens(OPENAI_MODEL, MAX_OUTPUT_TOKENS)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_BOT_KEY = os.getenv("DISCORD_BOT_KEY")
GUILD_ID = int(os.getenv("GUILD_ID"))
# Should use tokens here instead, this is a crude proxy.
MESSAGE_BATCH_SIZE = int(os.getenv("MESSAGE_BATCH_SIZE", "1000"))
OUTPUT_CHANNEL_ID = int(os.getenv("OUTPUT_CHANNEL_ID"))
SUMMARY_INTERVAL = int(os.getenv("SUMMARY_INTERVAL", "86400"))
MIN_MESSAGES_TO_SUMMARIZE = int(os.getenv("MIN_MESSAGES_TO_SUMMARIZE", "0"))

# OpenAI client
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


@dataclass(frozen=True)
class ChannelInfo:
    name: str
    id: int


@dataclass
class ChannelMessage:
    author: str
    content: str
    channel: ChannelInfo
    timestamp: datetime


async def summarize(
    prompt: str,
    iterative_prompt_suffix: str,
    last_summary: str,
    msgs_in_batch: List[str],
    channel: ChannelInfo,
    since: datetime,
    batch_number: int,
    output_channel,
):
    response = await openai_client.chat.completions.create(
        max_tokens=MAX_OUTPUT_TOKENS,
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": prompt
                if last_summary is None
                else prompt + iterative_prompt_suffix,
            },
            {
                "role": "user",
                "content": "".join(msgs_in_batch),
            },
        ],
    )
    # Instead of this crude crop, somehow use max_tokens in a better way to ensure a small response <2000 chars.
    prefix = (
        f"Summary of <#{channel.id}> activity since <t:{str(since.timestamp()).split('.')[0]}>:\n\n"
        if batch_number == 0
        else ""
    )
    to_send = prefix + response.choices[0].message.content[:1900]
    await output_channel.send(to_send)


class MyClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def setup_hook(self) -> None:
        self.encoding = tiktoken.encoding_for_model(OPENAI_MODEL)
        self.bg_task = self.loop.create_task(self.my_background_task())

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")

    async def my_background_task(self):
        await self.wait_until_ready()
        while not self.is_closed():
            await self.summarise()
            await asyncio.sleep(SUMMARY_INTERVAL)

    async def summarise(self):
        guild = self.get_guild(GUILD_ID)
        output_channel = utils.get(guild.channels, id=OUTPUT_CHANNEL_ID)
        if output_channel is None:
            print(f"Error: could not find channel with name {output_channel}")

        await output_channel.send(
            f"""
            Summarizing the last {SUMMARY_INTERVAL / 60 / 60} hours of conversation.\nAny channels with fewer than {MIN_MESSAGES_TO_SUMMARIZE} in this period will be ignored.
            """
        )

        prompt = f"""
          Summarize the text using bullet points.
          MENTION NAMES EXPLICITLY AND EXACTLY AS WRITTEN IN THE MESSAGES. Be succinct but go into detail where
          appropriate, e.g. if big decisions were made or if a topic was discussed at length.
          Interpret messages starting with '/' as Discord bot commands.
          The text is made up of Discord messages and is formatted as timestamp:channel:author:content.
          Typically, conversations do not span multiple channels, but that is not a hard rule.
        """
        iterative_prompt_suffix = f"""
          In addition, before messages, a summary of the previous {MESSAGE_BATCH_SIZE} messages is included.
          Use this summary as added context to aid in your summary of the input messages but DO NOT re-summarize it.
        """
        base_tokens_amount = (
            len(self.encoding.encode(prompt))
            + len(self.encoding.encode(iterative_prompt_suffix))
            + MAX_OUTPUT_TOKENS
            + OPENAI_TOKEN_BUFFER
        )
        since = datetime.now() - timedelta(seconds=SUMMARY_INTERVAL)
        channels = guild.text_channels
        for channel in channels:
            messages: List[ChannelMessage] = []
            permissions = channel.permissions_for(
                utils.get(guild.members, id=self.application_id)
            )
            if not permissions.read_messages:
                continue
            # This stops the bot summarizing previous summaries.
            if channel.id == OUTPUT_CHANNEL_ID:
                continue
            print(f"Processing messages channel={channel.name}")
            async for msg in channel.history(after=since, limit=None):
                messages.append(
                    ChannelMessage(
                        msg.author.name,
                        msg.content,
                        ChannelInfo(channel.name, channel.id),
                        msg.created_at,
                    )
                )
            if len(messages) < MIN_MESSAGES_TO_SUMMARIZE:
                continue
            last_summary = None
            batch_number = 0
            while len(messages) > 0:
                msgs_in_batch = []
                # Initialize with token allocation for the prompts and a prefix, and OPENAI_TOKEN_BUFFER.
                num_tokens_in_batch = base_tokens_amount
                for i, msg in enumerate(messages):
                    formatted_msg = f"{msg.timestamp.isoformat(timespec='seconds')}:{msg.channel.name}:{msg.author}:{msg.content}\n"
                    msg_tokens = len(self.encoding.encode(formatted_msg))
                    # Edge case: need to handle this somehow
                    if msg_tokens + base_tokens_amount > MAX_INPUT_TOKENS:
                        raise Exception(
                            f"Message too long to process: channel={channel.name} num_tokens={msg_tokens}"
                        )
                    if msg_tokens + num_tokens_in_batch > MAX_INPUT_TOKENS:
                        await summarize(
                            prompt,
                            iterative_prompt_suffix,
                            last_summary,
                            msgs_in_batch,
                            channel,
                            since,
                            batch_number,
                            output_channel,
                        )
                        messages = messages[i:]
                        batch_number += 1
                        break
                    else:
                        num_tokens_in_batch += msg_tokens
                        msgs_in_batch.append(formatted_msg)
                # Process final batch
                await summarize(
                    prompt,
                    iterative_prompt_suffix,
                    last_summary,
                    msgs_in_batch,
                    channel,
                    since,
                    batch_number,
                    output_channel,
                )
                break


intents = Intents.default()
intents.message_content = True
intents.messages = True
intents.guild_messages = True
intents.guild_reactions = True
client = MyClient(intents=intents)
# TODO: run this in such a way that Exception cause the process to terminate
client.run(DISCORD_BOT_KEY)
