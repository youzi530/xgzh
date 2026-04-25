"""日志：基于 loguru 的 JSON 输出，自动注入 request_id."""

from __future__ import annotations

import json
import sys
from typing import Any

from loguru import logger


def _json_sink(message: Any) -> None:
    record = message.record
    payload = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "logger": record["name"],
        "msg": record["message"],
    }
    if record["extra"]:
        payload.update({k: v for k, v in record["extra"].items() if k != "request_id"})
        if "request_id" in record["extra"]:
            payload["request_id"] = record["extra"]["request_id"]
    if record["exception"]:
        payload["exception"] = str(record["exception"])
    print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(_json_sink, level=level, format="{message}", backtrace=False, diagnose=False)


__all__ = ["logger", "setup_logging"]
