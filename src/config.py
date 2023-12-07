import os
from dataclasses import dataclass
from enum import Enum
from typing import List

from dotenv import load_dotenv

DEFAULT_PROMPT = """
    Summarize the text using bullet points.
    MENTION NAMES EXPLICITLY AND EXACTLY AS WRITTEN IN THE MESSAGES. Be succinct but go into detail where
    appropriate, e.g. if big decisions were made or if a topic was discussed at length.
    Interpret messages starting with '/' as Discord bot commands.
    The text is made up of Discord messages and is formatted as timestamp:channel:author:content.
    Typically, conversations do not span multiple channels, but that is not a hard rule.
"""


@dataclass
class DiscordClientConfig:
    summary_interval: int
    summary_output_channel_id: int
    summary_msg_lower_limit: int
    summary_autostart: bool
    guild_id: int
    authorized_user_ids: List[int]
    client_key: str


class AIProvider(Enum):
    open_ai = "open_ai"


@dataclass
class AIConfig:
    prompt: str
    max_output_tokens: int
    provider: AIProvider
    model: str
    api_key: str


def load_required(name: str) -> str:
    if not (value := os.getenv(name)):
        raise Exception(f"No value for {name}")
    return value


def load_config() -> tuple[DiscordClientConfig, AIConfig]:
    if ENV_FILE := os.getenv("ENV_FILE"):
        load_dotenv(ENV_FILE)

    _summary_autostart = os.getenv("SUMMARY_AUTOSTART", "false")
    assert _summary_autostart in ["true", "false"]
    summary_autostart = True if _summary_autostart == "true" else False

    return (
        DiscordClientConfig(
            int(os.getenv("SUMMARY_INTERVAL", "86400")),
            int(load_required("SUMMARY_OUTPUT_CHANNEL_ID")),
            int(os.getenv("SUMMARY_MSG_LOWER_LIMIT", "0")),
            summary_autostart,
            int(load_required("GUILD_ID")),
            [
                int(u)
                for u in os.getenv("AUTHORIZED_USER_IDS", "").split(",")
                if u != ""
            ],
            load_required("DISCORD_CLIENT_KEY"),
        ),
        AIConfig(
            DEFAULT_PROMPT,
            int(os.getenv("MAX_OUTPUT_TOKENS", "200")),
            AIProvider(load_required("AI_PROVIDER")),
            load_required("AI_MODEL"),
            load_required("AI_API_KEY"),
        ),
    )
