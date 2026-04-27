from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


class DebugContextFilter(logging.Filter):
    """Добавляет значения по умолчанию для DEBUG-формата."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "class_name"):
            record.class_name = "System"
        if not hasattr(record, "func_name"):
            record.func_name = "unknown"
        return True


def setup_logging(log_dir: Path, topic: str) -> tuple[logging.Logger, Path, Path]:
    """Настраивает два лог-файла (INFO и DEBUG) по заданному шаблону."""
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp: str = datetime.now().strftime("%Y%m%d_%H")

    info_path: Path = log_dir / f"INFO_{topic}_{timestamp}.log"
    debug_path: Path = log_dir / f"DEBUG_{topic}_{timestamp}.log"

    logger = logging.getLogger("spod_exporter")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    info_handler = logging.FileHandler(info_path, encoding="utf-8")
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(
        logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    )

    # Формат DEBUG зафиксирован по ТЗ.
    debug_handler = logging.FileHandler(debug_path, encoding="utf-8")
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - [%(levelname)s] - %(message)s [class: %(class_name)s | def: %(func_name)s]"
        )
    )
    debug_handler.addFilter(DebugContextFilter())

    logger.addHandler(info_handler)
    logger.addHandler(debug_handler)

    # Дублируем INFO-события в консоль для удобного контроля процесса.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(console_handler)
    return logger, info_path, debug_path


def debug_extra(class_name: str, func_name: str) -> dict[str, str]:
    """Единый helper для обязательных полей DEBUG-формата."""
    return {"class_name": class_name, "func_name": func_name}

