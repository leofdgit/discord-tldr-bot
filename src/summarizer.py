import abc
from typing import List

import tiktoken

from .config import AIConfig


class Summarizer(abc.ABC):
    encoding: tiktoken.Encoding
    max_msg_tokens: int

    def __init__(self, config: AIConfig, *args, **kwargs):
        self.config = config

    @abc.abstractmethod
    async def summarize(self, messages: List[str]) -> str:
        pass
