from __future__ import annotations

import argparse
from pathlib import Path

from spod_exporter.config import load_config
from spod_exporter.logging_setup import setup_logging
from spod_exporter.pipeline import SpodPipeline


def parse_args() -> argparse.Namespace:
    """Разбор аргументов командной строки."""
    parser = argparse.ArgumentParser(description="Консолидация настроек SPOD в Excel/SQLite")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.json"),
        help="Путь к config.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Выполнить обработку без записи в SQLite raw/merged и без сохранения Excel",
    )
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=0,
        help="Число потоков для параллельной обработки (0 = авто)",
    )
    return parser.parse_args()


def main() -> None:
    """Точка входа CLI."""
    args = parse_args()
    config = load_config(args.config)
    runtime_cfg = config.setdefault("runtime", {})
    if args.dry_run:
        runtime_cfg["dry_run"] = True
    if args.parallel_workers and args.parallel_workers > 0:
        runtime_cfg["parallel_workers"] = args.parallel_workers

    logger, info_path, debug_path = setup_logging(
        Path(config["paths"]["log_dir"]), config["logging"]["topic"]
    )
    logger.info("Старт обработки. config=%s", args.config)
    logger.debug(
        "Инициализировано логирование INFO=%s DEBUG=%s",
        info_path,
        debug_path,
        extra={"class_name": "Main", "func_name": "main"},
    )

    pipeline = SpodPipeline(config, logger)
    db_path, excel_path = pipeline.run()
    logger.info("Готово. DB=%s EXCEL=%s", db_path, excel_path)


if __name__ == "__main__":
    main()

