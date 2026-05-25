from typing import Optional
import logging
import pydantic
import sys


def get_logger(name: str,
               log_level: int = logging.INFO,
               log_file_name: Optional[str] = None,
               log_to_console: bool = True) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    if log_to_console:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        stream_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        stream_handler.setFormatter(stream_formatter)
        logger.addHandler(stream_handler)

    if log_file_name:
        file_handler = logging.FileHandler(log_file_name)
        file_handler.setLevel(log_level)
        file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


class Message(pydantic.BaseModel):
    content: str
    role: str = "user"


class ChatRequest(pydantic.BaseModel):
    messages: list[Message]
    case_id: str  # ← legal bot addition