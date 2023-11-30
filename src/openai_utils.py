from openai import AsyncOpenAI
import tiktoken

from summarizer import Summarizer

from typing import List

# Due to uncertainty around the way that OpenAI tokenizes text server-side, include a pessemistic buffer.
OPENAI_TOKEN_BUFFER = 100

# TODO: add more
MODEL_TO_MAX_TOKENS = {
    "gpt-3.5-turbo": 4096,
    "gpt-3.5-turbo-1106": 16385,
    "gpt-4": 8192,
    "gpt-4-1106-preview": 128000,
}


class SummaryClient(AsyncOpenAI, Summarizer):
    def __init__(
        self, prompt: str, max_output_tokens: int, model: str, *args, **kwargs
    ):
        Summarizer.__init__(self, prompt, max_output_tokens, model)
        AsyncOpenAI.__init__(self, *args, **kwargs)
        self.__encoding = tiktoken.encoding_for_model(model)
        self.__max_msg_tokens = self._max_msg_tokens()

    async def summarize(self, messages: List[str]) -> str:
        response = await self.chat.completions.create(
            max_tokens=self.max_output_tokens,
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": self.prompt,
                },
                {
                    "role": "user",
                    "content": "".join(messages),
                },
            ],
        )

        if (content := response.choices[0].message.content) is None:
            raise Exception("Received no content.")
        return content

    def max_msg_tokens(self) -> int:
        return self.__max_msg_tokens

    def _max_msg_tokens(self) -> int:
        max_tokens_for_model = MODEL_TO_MAX_TOKENS[self.model]
        if not max_tokens_for_model:
            raise Exception(f"Unsupported model: {self.model}")
        base_tokens_amount = (
            len(self.encoding.encode(self.prompt))
            + self.max_output_tokens
            + OPENAI_TOKEN_BUFFER
        )
        res = max_tokens_for_model - base_tokens_amount
        # Arbitrary, but avoid max_output_tokens being far too high for the choice of model
        min_input_tokens = 500
        if res < min_input_tokens:
            raise Exception(
                f"Too few tokens allocated for input. max_tokens_for_model={max_tokens_for_model}, max_output_tokens={self.max_output_tokens}, input_tokens={res}. max_tokens_for_model - max_output_tokens must be greater than {min_input_tokens}"
            )
        return res

    def encoding(self) -> tiktoken.Encoding:
        return self.__encoding
