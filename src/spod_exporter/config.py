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

    runtime_cfg = config.setdefault("runtime", {})
    parallel_workers = runtime_cfg.get("parallel_workers", "auto")
    if not (
        parallel_workers == "auto"
        or (isinstance(parallel_workers, int) and parallel_workers > 0)
    ):
        raise ValueError(
            "runtime.parallel_workers должен быть 'auto' или целым числом > 0"
        )
    runtime_cfg.setdefault("dry_run", False)

    merge_cfg = config.setdefault("merge", {})
    ref_stand = merge_cfg.get("reference_row_stand", "PROM")
    if ref_stand and ref_stand not in config.get("stands", []):
        raise ValueError(
            f"merge.reference_row_stand={ref_stand!r} отсутствует в списке stands"
        )

    config.setdefault("excel", {}).setdefault("diff_report_sheet", {}).setdefault(
        "enabled", True
    )
    return config

