from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_config(config_path: Path) -> dict[str, Any]:
    """Загружает JSON-конфиг и проверяет базовые обязательные поля."""
    with config_path.open("r", encoding="utf-8") as file:
        config: dict[str, Any] = json.load(file)

    required_paths: tuple[str, ...] = (
        "input_root",
        "output_excel_dir",
        "output_db_dir",
        "log_dir",
    )
    for path_key in required_paths:
        if path_key not in config["paths"]:
            raise ValueError(f"В config.json отсутствует paths.{path_key}")

    if not config.get("stands"):
        raise ValueError("В config.json отсутствует список stands")
    if not config.get("entities"):
        raise ValueError("В config.json отсутствует секция entities")
    return config

