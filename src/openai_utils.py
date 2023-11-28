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
