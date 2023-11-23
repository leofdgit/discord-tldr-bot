from dataclasses import dataclass
from discord import Intents, Client, utils
from openai import OpenAI
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from typing import List
import asyncio

# Require permissions int: 34359938048

# Attempts to load environment variables from the file specified by environment variable
# ENV_FILE if it is set.
if ENV_FILE := os.getenv("ENV_FILE"):
    load_dotenv(ENV_FILE)

# Read the list of authorized user IDs from an environment variable.
# The value of the AUTHORIZED_USERS env var should be a csv-delimited array
# of Discord IDs.
# E.g. 12345,67890
# which would allow the two users whose Discord IDs are 12345 and 67890 to
# use the command.
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "200"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_BOT_KEY = os.getenv("DISCORD_BOT_KEY")
GUILD_ID = int(os.getenv("GUILD_ID"))
MESSAGE_BATCH_SIZE = int(os.getenv("MESSAGE_BATCH_SIZE", "1000"))
OUTPUT_CHANNEL_ID = int(os.getenv("OUTPUT_CHANNEL_ID"))


# OpenAI client
openai_client = OpenAI(api_key=OPENAI_API_KEY)


@dataclass
class ChannelMessage:
    author: str
    content: str
    channel: str
    timestamp: datetime


class MyClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def setup_hook(self) -> None:
        self.bg_task = self.loop.create_task(self.my_background_task())

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print("------")

    async def my_background_task(self):
        await self.wait_until_ready()
        print("here")
        while not self.is_closed():
            await self.summarise()
            await asyncio.sleep(10)

    async def summarise(self):
        print("here too")
        guild = self.get_guild(GUILD_ID)
        output_channel = utils.get(guild.channels, id=OUTPUT_CHANNEL_ID)
        if output_channel is None:
            print(f"Error: could not find channel with name {output_channel}")
        prompt = f"""
          Summarize the text using bullet points. Translate text to English before
          summarizing if appropriate.
          MENTION NAMES EXPLICITLY AND EXACTLY AS WRITTEN IN THE MESSAGES. Be succinct but go into detail where
          appropriate, e.g. if big decisions were made or if a topic was discussed at length, and
          use bullet points. Interpret messages starting with '/' as Discord bot commands.
          The text is made up of Discord messages and is formatted as timestamp:channel:author:content.
        """
        iterative_prompt_suffix = f"""
          In addition, before messages, a summary of the previous {MESSAGE_BATCH_SIZE} messages is included.
          Use this summary to aid in your creation of an even better summary that takes into account both the 
          previous summary and the new messages.
        """
        since = datetime.now() - timedelta(hours=24)
        print("here too")
        messages: List[ChannelMessage] = []
        print("here too")
        print("here too")
        channels = guild.text_channels
        for channel in channels:
            print(f"Processing messages channel={channel.name}")
            async for msg in channel.history(limit=None, after=since):
                if msg.author.bot and msg.author.id == self.application_id:
                    continue
                messages.append(
                    ChannelMessage(
                        msg.author.name, msg.content, channel.name, msg.created_at
                    )
                )
        messages.sort(key=lambda x: x.timestamp)

        last_summary = None
        for i, batch in enumerate(
            [
                messages[i : i + MESSAGE_BATCH_SIZE]
                for i in range(0, len(messages), MESSAGE_BATCH_SIZE)
            ]
        ):
            print(f"Processing batch {i}")
            response = openai_client.chat.completions.create(
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
                                f"{m.timestamp.isoformat(timespec='seconds')}:{m.channel}:{m.author}:{m.content}"
                                for m in batch
                            ]
                        ),
                    },
                ],
            )
        await output_channel.send(response.choices[0].message.content)


intents = Intents.default()
intents.message_content = True
intents.messages = True
intents.guild_messages = True
intents.guild_reactions = True
client = MyClient(intents=intents)
client.run(DISCORD_BOT_KEY)
