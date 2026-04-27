from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileDescriptor:
    """Метаданные входного CSV-файла."""

    stand: str
    entity: str
    file_path: Path
    file_name: str
    file_hash: str
    file_size: int


@dataclass(frozen=True)
class ParsedRow:
    """Сырые данные строки CSV до объединения."""

    stand: str
    entity: str
    row_num: int
    data: dict[str, str]
    business_key: str
    row_hash: str


@dataclass(frozen=True)
class MergedRow:
    """Итоговая объединенная запись для Excel/SQLite."""

    entity: str
    business_key: str
    row_hash: str
    source_stands: str
    source_count: int
    is_equal_all: bool
    diff_group_key: str
    merged_data: dict[str, Any]

