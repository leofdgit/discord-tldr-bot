import abc
from typing import List

import tiktoken

from config import AIConfig


class Summarizer(abc.ABC):
    def __init__(self, config: AIConfig, *args, **kwargs):
        self.config = config

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
