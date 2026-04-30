from __future__ import annotations

import logging
import sqlite3
import threading
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


class SQLiteLogHandler(logging.Handler):
    """Пишет логи в SQLite с раздельными колонками для удобной фильтрации."""

    def __init__(self, db_path: Path, level: int) -> None:
        super().__init__(level=level)
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS program_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                level_name TEXT NOT NULL,
                level_no INTEGER NOT NULL,
                logger_name TEXT NOT NULL,
                message TEXT NOT NULL,
                class_name TEXT,
                class_func_name TEXT,
                python_module TEXT,
                python_func_name TEXT,
                source_file TEXT,
                source_path TEXT,
                source_line_no INTEGER,
                process_id INTEGER,
                thread_id INTEGER,
                thread_name TEXT
            )
            """
        )
        self._conn.commit()

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        created_at = datetime.fromtimestamp(record.created).isoformat(timespec="seconds")
        class_name = str(getattr(record, "class_name", "System"))
        class_func_name = str(getattr(record, "func_name", "unknown"))
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO program_logs(
                    created_at, level_name, level_no, logger_name, message,
                    class_name, class_func_name, python_module, python_func_name,
                    source_file, source_path, source_line_no,
                    process_id, thread_id, thread_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    str(record.levelname),
                    int(record.levelno),
                    str(record.name),
                    str(message),
                    class_name,
                    class_func_name,
                    str(record.module),
                    str(record.funcName),
                    str(record.filename),
                    str(record.pathname),
                    int(record.lineno),
                    int(record.process),
                    int(record.thread),
                    str(record.threadName),
                ),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
        super().close()


def setup_logging(
    log_dir: Path, topic: str, log_file_type: str = "INFO"
) -> tuple[logging.Logger, Path, Path]:
    """Настраивает один лог-файл (INFO или DEBUG) по заданному шаблону."""
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp: str = datetime.now().strftime("%Y%m%d_%H")

    info_path: Path = log_dir / f"INFO_{topic}_{timestamp}.log"
    debug_path: Path = log_dir / f"DEBUG_{topic}_{timestamp}.log"

    logger = logging.getLogger("spod_exporter")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    log_file_type_normalized = str(log_file_type).strip().upper()
    if log_file_type_normalized == "DEBUG":
        # Формат DEBUG зафиксирован по ТЗ.
        file_handler = logging.FileHandler(debug_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - [%(levelname)s] - %(message)s [class: %(class_name)s | def: %(func_name)s]"
            )
        )
        file_handler.addFilter(DebugContextFilter())
        active_path = debug_path
    else:
        file_handler = logging.FileHandler(info_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
        )
        active_path = info_path

    logger.addHandler(file_handler)

    db_dir = log_dir / "DB"
    db_path = db_dir / "program_logs.sqlite"
    try:
        sqlite_handler = SQLiteLogHandler(db_path=db_path, level=file_handler.level)
        logger.addHandler(sqlite_handler)
    except Exception as exc:
        logger.warning(
            "SQLite-логирование отключено из-за ошибки инициализации: %s",
            exc,
        )

    # Дублируем INFO-события в консоль для удобного контроля процесса.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(console_handler)
    return logger, active_path, db_path


def debug_extra(class_name: str, func_name: str) -> dict[str, str]:
    """Единый helper для обязательных полей DEBUG-формата."""
    return {"class_name": class_name, "func_name": func_name}

