import os


def load_required(name: str) -> str:
    if not (value := os.getenv(name)):
        raise Exception(f"No value for {name}")
    return value
