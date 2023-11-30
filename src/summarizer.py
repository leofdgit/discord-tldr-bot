import abc
from typing import List

import tiktoken

DEFAULT_PROMPT = """
    Summarize the text using bullet points.
    MENTION NAMES EXPLICITLY AND EXACTLY AS WRITTEN IN THE MESSAGES. Be succinct but go into detail where
    appropriate, e.g. if big decisions were made or if a topic was discussed at length.
    Interpret messages starting with '/' as Discord bot commands.
    The text is made up of Discord messages and is formatted as timestamp:channel:author:content.
    Typically, conversations do not span multiple channels, but that is not a hard rule.
"""


class Summarizer(abc.ABC):
    def __init__(self, prompt: str, max_output_tokens: int, model: str):
        self.prompt = prompt
        self.max_output_tokens = max_output_tokens
        self.model = model

    @abc.abstractmethod
    async def summarize(self, messages: List[str]) -> str:
        pass

    @property
    @abc.abstractmethod
    def max_msg_tokens(self) -> int:
        """
        The token count of the messages to be summarized cannot exceed this number.
        """
        pass

    @property
    @abc.abstractmethod
    def encoding(self) -> tiktoken.Encoding:
        """
        The number of tokens required
        """
        pass
