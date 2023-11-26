from dataclasses import dataclass
from discord import Intents, Client, utils
from openai import AsyncOpenAI
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import List
import asyncio
from itertools import groupby
import math

if ENV_FILE := os.getenv("ENV_FILE"):
    load_dotenv(ENV_FILE)

MAX_TOKENS = int(os.getenv("MAX_TOKENS", "200"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
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


class MyClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def setup_hook(self) -> None:
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
          Use this summary as added context to aid in your summary of the input messages.
        """
        since = datetime.now() - timedelta(seconds=SUMMARY_INTERVAL)
        messages: List[ChannelMessage] = []
        channels = guild.text_channels
        for channel in channels:
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
        messages = groupby(messages, key=lambda x: x.channel)
        active_channel_messages = {}
        for channel, group in messages:
            msgs = list(group)
            if len(msgs) < MIN_MESSAGES_TO_SUMMARIZE:
                continue
            active_channel_messages[channel] = sorted(msgs, key=lambda x: x.timestamp)
        for channel, msgs in active_channel_messages.items():
            last_summary = None
            for i, batch in enumerate(
                [
                    msgs[i : i + MESSAGE_BATCH_SIZE]
                    for i in range(0, len(msgs), MESSAGE_BATCH_SIZE)
                ]
            ):
                print(
                    f"Processing batch {i}/{math.ceil(len(msgs) / MESSAGE_BATCH_SIZE)} channel={channel.name}"
                )
                response = await openai_client.chat.completions.create(
                    max_tokens=MAX_TOKENS,
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
                            "content": "\n".join(
                                [
                                    f"{m.timestamp.isoformat(timespec='seconds')}:{m.channel.name}:{m.author}:{m.content}"
                                    for m in batch
                                ]
                            ),
                        },
                    ],
                )
                # Instead of this crude crop, somehow use max_tokens in a better way to ensure a small response <2000 chars.
                prefix = (
                    f"Summary of <#{channel.id}> activity since <t:{str(since.timestamp()).split('.')[0]}>:\n\n"
                    if i == 0
                    else ""
                )
                to_send = prefix + response.choices[0].message.content[:1900]
                print(to_send)
                # await output_channel.send(to_send)


intents = Intents.default()
intents.message_content = True
intents.messages = True
intents.guild_messages = True
intents.guild_reactions = True
client = MyClient(intents=intents)
client.run(DISCORD_BOT_KEY)
