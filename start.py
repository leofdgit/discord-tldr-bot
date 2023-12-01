from dataclasses import dataclass
from discord import Intents, Client, utils, TextChannel
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import asyncio

from src.config import AIProvider, load_config
from src.discord_client import DiscordClient, register_commands
from src.openai_utils import SummaryClient as OpenAISummaryClient
from src.summarizer import Summarizer


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


def ai_client(ai_provider: AIProvider) -> type[Summarizer]:
    match ai_provider:
        case AIProvider.open_ai:
            return OpenAISummaryClient


discord_config, ai_config = load_config()

# OpenAI client
summarizer = ai_client(ai_config.provider)(ai_config, api_key=ai_config.api_key)

intents = Intents.default()
intents.message_content = True
intents.messages = True
intents.guild_messages = True
intents.guild_reactions = True
client = DiscordClient(discord_config, summarizer, intents=intents)
register_commands(client)
# TODO: run this in such a way that Exception cause the process to terminate
client.run(discord_config.client_key)
