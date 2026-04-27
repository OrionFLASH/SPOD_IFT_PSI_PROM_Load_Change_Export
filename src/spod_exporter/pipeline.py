from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from .logging_setup import debug_extra
from .models import FileDescriptor, MergedRow, ParsedRow


class SpodPipeline:
    """Главный orchestrator загрузки CSV, консолидации и выгрузки."""

    def __init__(self, config: dict[str, Any], logger: Any) -> None:
        self.config = config
        self.logger = logger
        self.run_id: str = datetime.now().strftime("%Y%m%d_%H%M%S")

        self.paths: dict[str, Path] = {
            "input_root": Path(config["paths"]["input_root"]),
            "output_excel_dir": Path(config["paths"]["output_excel_dir"]),
            "output_db_dir": Path(config["paths"]["output_db_dir"]),
        }
        self.entities: dict[str, dict[str, Any]] = config["entities"]
        self.stands: list[str] = config["stands"]

        self.paths["output_excel_dir"].mkdir(parents=True, exist_ok=True)
        self.paths["output_db_dir"].mkdir(parents=True, exist_ok=True)

        db_name: str = config["sqlite"]["db_name"]
        self.db_path: Path = self.paths["output_db_dir"] / db_name

    def run(self) -> tuple[Path, Path]:
        """Запускает полный цикл обработки по ТЗ."""
        conn = sqlite3.connect(self.db_path)
        try:
            self._init_db(conn)
            files = self._scan_files()
            parsed = self._parse_all_rows(files, conn)
            merged = self._merge_rows(parsed)
            excel_path = self._export_excel(merged)
            self._save_run(conn, "SUCCESS", excel_path)
            conn.commit()
            self.logger.info("Обработка завершена успешно")
            return self.db_path, excel_path
        except Exception:
            self._save_run(conn, "FAILED", None)
            conn.commit()
            raise
        finally:
            conn.close()

    def _init_db(self, conn: sqlite3.Connection) -> None:
        """Создает таблицы SQLite и индексы дедупликации."""
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                result_status TEXT NOT NULL,
                excel_path TEXT,
                log_info_path TEXT,
                log_debug_path TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ingested_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                stand TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_hash_sha256 TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                loaded_at TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_ingested_files_hash ON ingested_files(file_hash_sha256)"
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                stand TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                row_num INTEGER NOT NULL,
                row_json TEXT NOT NULL,
                row_hash TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS merged_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                business_key TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                source_stands TEXT NOT NULL,
                source_count INTEGER NOT NULL,
                is_equal_all INTEGER NOT NULL,
                diff_group_key TEXT NOT NULL,
                merged_json TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            INSERT OR REPLACE INTO runs(run_id, started_at, result_status)
            VALUES(?, ?, ?)
            """,
            (self.run_id, datetime.now().isoformat(), "RUNNING"),
        )

    def _save_run(
        self, conn: sqlite3.Connection, status: str, excel_path: Path | None
    ) -> None:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE runs
            SET finished_at = ?, result_status = ?, excel_path = ?
            WHERE run_id = ?
            """,
            (
                datetime.now().isoformat(),
                status,
                str(excel_path) if excel_path else None,
                self.run_id,
            ),
        )

    def _scan_files(self) -> list[FileDescriptor]:
        """Сканирует входные каталоги и сопоставляет имена файлов типам сущностей."""
        descriptors: list[FileDescriptor] = []
        input_root: Path = self.paths["input_root"]

        for stand in self.stands:
            stand_dir = input_root / stand
            if not stand_dir.exists():
                raise FileNotFoundError(f"Не найден каталог стенда: {stand_dir}")

            for file_path in sorted(stand_dir.glob("*.csv")):
                entity = self._resolve_entity(file_path.name)
                file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
                descriptors.append(
                    FileDescriptor(
                        stand=stand,
                        entity=entity,
                        file_path=file_path,
                        file_name=file_path.name,
                        file_hash=file_hash,
                        file_size=file_path.stat().st_size,
                    )
                )

        self.logger.info("Найдено входных файлов: %s", len(descriptors))
        return descriptors

    def _resolve_entity(self, file_name: str) -> str:
        """Определяет тип сущности по префиксу имени файла."""
        upper_name = file_name.upper()
        for entity, details in self.entities.items():
            if upper_name.startswith(details["file_prefix"].upper()):
                return entity
        raise ValueError(f"Не удалось определить entity для файла: {file_name}")

    def _parse_all_rows(
        self, files: list[FileDescriptor], conn: sqlite3.Connection
    ) -> dict[str, list[ParsedRow]]:
        """Парсит CSV, формирует ключи/хэши строк и пишет данные в SQLite."""
        by_entity: dict[str, list[ParsedRow]] = defaultdict(list)
        cursor = conn.cursor()

        for descriptor in files:
            is_new_hash = self._is_new_file_hash(cursor, descriptor.file_hash)
            status = "NEW" if is_new_hash else "SKIPPED_DUPLICATE"
            cursor.execute(
                """
                INSERT OR IGNORE INTO ingested_files(
                    run_id, stand, entity_type, file_path, file_name,
                    file_hash_sha256, file_size, loaded_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.run_id,
                    descriptor.stand,
                    descriptor.entity,
                    str(descriptor.file_path),
                    descriptor.file_name,
                    descriptor.file_hash,
                    descriptor.file_size,
                    datetime.now().isoformat(),
                    status,
                ),
            )

            with descriptor.file_path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file, delimiter=";")
                for row_num, row in enumerate(reader, start=2):
                    normalized = self._normalize_row(row)
                    business_key = self._build_business_key(descriptor.entity, normalized)
                    row_hash = self._hash_json(normalized)
                    parsed = ParsedRow(
                        stand=descriptor.stand,
                        entity=descriptor.entity,
                        row_num=row_num,
                        data=normalized,
                        business_key=business_key,
                        row_hash=row_hash,
                    )
                    by_entity[descriptor.entity].append(parsed)

                    if is_new_hash:
                        cursor.execute(
                            """
                            INSERT INTO raw_rows(run_id, stand, entity_type, row_num, row_json, row_hash)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                self.run_id,
                                descriptor.stand,
                                descriptor.entity,
                                row_num,
                                json.dumps(normalized, ensure_ascii=False),
                                row_hash,
                            ),
                        )

            self.logger.debug(
                "Обработан файл %s (%s)",
                descriptor.file_name,
                status,
                extra=debug_extra("SpodPipeline", "_parse_all_rows"),
            )

        return by_entity

    def _is_new_file_hash(self, cursor: sqlite3.Cursor, file_hash: str) -> bool:
        cursor.execute(
            "SELECT 1 FROM ingested_files WHERE file_hash_sha256 = ? LIMIT 1", (file_hash,)
        )
        return cursor.fetchone() is None

    def _normalize_row(self, row: dict[str, str | None]) -> dict[str, str]:
        """Канонизирует CSV-строку для корректного сравнения между стендами."""
        normalized: dict[str, str] = {}
        for key, value in row.items():
            # Сохраняем порядок и исходные колонки, но приводим пустые значения к единообразию.
            normalized[key] = "" if value is None else value.strip()
        return normalized

    def _build_business_key(self, entity: str, row: dict[str, str]) -> str:
        """Формирует бизнес-ключ по конфигу; fallback — полный hash строки."""
        key_fields: list[str] = self.entities[entity]["business_key"]
        values: list[str] = [row.get(field, "") for field in key_fields]
        if all(value == "" for value in values):
            return f"HASH:{self._hash_json(row)}"
        return "|".join(values)

    def _hash_json(self, payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _merge_rows(self, parsed_rows: dict[str, list[ParsedRow]]) -> dict[str, list[MergedRow]]:
        """Объединяет данные по business_key + row_hash и формирует служебные признаки."""
        result: dict[str, list[MergedRow]] = {}

        for entity, rows in parsed_rows.items():
            grouped: dict[tuple[str, str], list[ParsedRow]] = defaultdict(list)
            for row in rows:
                grouped[(row.business_key, row.row_hash)].append(row)

            merged_rows: list[MergedRow] = []
            by_business_key_counts: dict[str, int] = defaultdict(int)

            for (business_key, row_hash), group_rows in grouped.items():
                stands = sorted({row.stand for row in group_rows})
                source_stands = "-".join(stands)
                source_count = len(stands)
                by_business_key_counts[business_key] += 1
                diff_group_key = f"{entity}:{business_key}:{by_business_key_counts[business_key]}"
                base_data = dict(group_rows[0].data)
                base_data["source_stands"] = source_stands
                base_data["source_count"] = str(source_count)
                base_data["is_equal_all"] = "1" if source_count == len(self.stands) else "0"
                base_data["diff_group_key"] = diff_group_key

                merged_rows.append(
                    MergedRow(
                        entity=entity,
                        business_key=business_key,
                        row_hash=row_hash,
                        source_stands=source_stands,
                        source_count=source_count,
                        is_equal_all=source_count == len(self.stands),
                        diff_group_key=diff_group_key,
                        merged_data=base_data,
                    )
                )

            merged_rows.sort(key=lambda item: (item.business_key, item.row_hash))
            result[entity] = merged_rows
        return result

    def _export_excel(self, merged: dict[str, list[MergedRow]]) -> Path:
        """Создает Excel-файл: 1 лист на сущность + служебные колонки."""
        workbook = Workbook()
        workbook.remove(workbook.active)

        summary = workbook.create_sheet("SUMMARY")
        summary_headers = ["entity", "rows_total", "rows_equal_all", "rows_different"]
        summary.append(summary_headers)

        for entity in self.entities.keys():
            sheet = workbook.create_sheet(entity)
            rows = merged.get(entity, [])

            if rows:
                base_headers = list(rows[0].merged_data.keys())
            else:
                base_headers = []
            sheet.append(base_headers)

            equal_all = 0
            for item in rows:
                if item.is_equal_all:
                    equal_all += 1
                sheet.append([item.merged_data.get(header, "") for header in base_headers])

            summary.append([entity, len(rows), equal_all, len(rows) - equal_all])

        file_name = datetime.now().strftime(
            self.config["excel"]["output_name_pattern"]
        )
        excel_path = self.paths["output_excel_dir"] / file_name
        workbook.save(excel_path)
        self.logger.info("Excel сформирован: %s", excel_path)
        return excel_path

