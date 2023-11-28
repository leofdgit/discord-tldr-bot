from dataclasses import dataclass
from discord import Intents, Client, utils, TextChannel
from openai import AsyncOpenAI
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import List, Text, Union
import asyncio
import tiktoken

from src.env import load_required
from src.openai_utils import max_input_tokens, OPENAI_TOKEN_BUFFER


if ENV_FILE := os.getenv("ENV_FILE"):
    load_dotenv(ENV_FILE)

MAX_OUTPUT_TOKENS = int(os.getenv("MAX_TOKENS", "200"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
MAX_INPUT_TOKENS = max_input_tokens(OPENAI_MODEL, MAX_OUTPUT_TOKENS)
OPENAI_API_KEY = load_required("OPENAI_API_KEY")
DISCORD_BOT_KEY = load_required("DISCORD_BOT_KEY")
GUILD_ID = int(load_required("GUILD_ID"))
OUTPUT_CHANNEL_ID = int(load_required("OUTPUT_CHANNEL_ID"))
SUMMARY_INTERVAL = int(os.getenv("SUMMARY_INTERVAL", "86400"))
MIN_MESSAGES_TO_SUMMARIZE = int(os.getenv("MIN_MESSAGES_TO_SUMMARIZE", "0"))

# OpenAI client
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = f"""
    Summarize the text using bullet points.
    MENTION NAMES EXPLICITLY AND EXACTLY AS WRITTEN IN THE MESSAGES. Be succinct but go into detail where
    appropriate, e.g. if big decisions were made or if a topic was discussed at length.
    Interpret messages starting with '/' as Discord bot commands.
    The text is made up of Discord messages and is formatted as timestamp:channel:author:content.
    Typically, conversations do not span multiple channels, but that is not a hard rule.
"""


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
    system_prompt: str,
    msgs_in_batch: List[str],
    output_channel: TextChannel,
) -> None:
    response = await openai_client.chat.completions.create(
        max_tokens=MAX_OUTPUT_TOKENS,
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": "".join(msgs_in_batch),
            },
        ],
    )
    if (content := response.choices[0].message.content) is None:
        await output_channel.send("Something went wrong!")
        raise Exception("Received no content.")
    # Instead of this crude crop, somehow use max_tokens in a better way to ensure a small response <2000 chars.
    to_send = content[:1900]
    await output_channel.send(to_send)


class MyClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def setup_hook(self) -> None:
        self.encoding = tiktoken.encoding_for_model(OPENAI_MODEL)
        self.bg_task = self.loop.create_task(self.my_background_task())

    async def on_ready(self):
        assert self.user is not None, f"Not logged in!"
        print(f"Logged in as {self.user} (ID: {self.user.id})")

    async def my_background_task(self):
        await self.wait_until_ready()
        while not self.is_closed():
            await self.summarise()
            await asyncio.sleep(SUMMARY_INTERVAL)

    async def summarise(self):
        if not self.application_id:
            raise Exception("Not logged in!")
        guild = self.get_guild(GUILD_ID)
        if not guild:
            raise Exception(f"Failed to get guild with id {GUILD_ID}")
        output_channel = utils.get(guild.channels, id=OUTPUT_CHANNEL_ID)
        if output_channel is None:
            raise Exception(f"Error: could not find channel with name {output_channel}")

        if not isinstance(output_channel, TextChannel):
            raise Exception("Output channel must be a text channel.")
        since = datetime.now() - timedelta(seconds=SUMMARY_INTERVAL)
        await output_channel.send(
            f"""
            Summarizing server activity since <t:{str(since.timestamp()).split('.')[0]}>. Any channels with fewer than {MIN_MESSAGES_TO_SUMMARIZE} messages in this period will be ignored.
            """
        )

        base_tokens_amount = (
            len(self.encoding.encode(SYSTEM_PROMPT))
            + MAX_OUTPUT_TOKENS
            + OPENAI_TOKEN_BUFFER
        )
        since = datetime.now() - timedelta(seconds=SUMMARY_INTERVAL)
        channels = guild.text_channels
        for channel in channels:
            bot_member = utils.get(guild.members, id=self.application_id)
            if not bot_member:
                raise Exception("Unable to find bot Discord user.")
            permissions = channel.permissions_for(bot_member)
            if not permissions.read_messages:
                continue
            # This stops the bot summarizing previous summaries.
            if channel.id == OUTPUT_CHANNEL_ID:
                continue
            print(f"Processing messages channel={channel.name}")
            messages = [
                ChannelMessage(
                    msg.author.name,
                    msg.content,
                    ChannelInfo(channel.name, channel.id),
                    msg.created_at,
                )
                async for msg in channel.history(after=since, limit=None)
            ]
            if len(messages) < MIN_MESSAGES_TO_SUMMARIZE:
                continue
            await output_channel.send(f"Summary of <#{channel.id}>:\n\n")
            msgs_in_batch = []
            # Initialize with token allocation for the prompts and a prefix, and OPENAI_TOKEN_BUFFER.
            num_tokens_in_batch = base_tokens_amount
            while len(messages) > 0:
                msg = messages[0]
                formatted_msg = f"{msg.timestamp.isoformat(timespec='seconds')}:{msg.channel.name}:{msg.author}:{msg.content}\n"
                msg_tokens = len(self.encoding.encode(formatted_msg))
                # Edge case: need to handle this somehow
                if msg_tokens + base_tokens_amount > MAX_INPUT_TOKENS:
                    raise Exception(
                        f"Message too long to process: channel={channel.name} num_tokens={msg_tokens}"
                    )
                if msg_tokens + num_tokens_in_batch > MAX_INPUT_TOKENS:
                    await summarize(
                        SYSTEM_PROMPT,
                        msgs_in_batch,
                        output_channel,
                    )
                    msgs_in_batch = []
                    num_tokens_in_batch = base_tokens_amount
                else:
                    num_tokens_in_batch += msg_tokens
                    msgs_in_batch.append(formatted_msg)
                    messages.pop(0)
            # Process final batch
            if msgs_in_batch:
                await summarize(
                    SYSTEM_PROMPT,
                    msgs_in_batch,
                    output_channel,
                )


intents = Intents.default()
intents.message_content = True
intents.messages = True
intents.guild_messages = True
intents.guild_reactions = True
client = MyClient(intents=intents)
# TODO: run this in such a way that Exception cause the process to terminate
client.run(DISCORD_BOT_KEY)
