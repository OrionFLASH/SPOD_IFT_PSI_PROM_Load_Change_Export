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
from openpyxl.styles import Alignment, Font, PatternFill

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
            self.logger.info("Проверка входных файлов перед стартом...")
            self._init_db(conn)
            files = self._scan_files()
            self._log_found_files_summary(files)
            parsed, parse_stats = self._parse_all_rows(files, conn)
            self._log_parse_summary(parse_stats)
            merged, merge_stats = self._merge_rows(parsed)
            self._log_merge_summary(merge_stats)
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
        """Сканирует входные каталоги по явным именам файлов из config.json."""
        descriptors: list[FileDescriptor] = []
        input_root: Path = self.paths["input_root"]
        missing_items: list[str] = []

        for stand in self.stands:
            stand_dir = input_root / stand
            if not stand_dir.exists():
                missing_items.append(f"Каталог стенда отсутствует: {stand_dir}")
                continue

            for entity, details in self.entities.items():
                file_name = details["file_names"][stand]
                file_path = stand_dir / file_name
                if not file_path.exists():
                    missing_items.append(
                        f"Не найден файл entity={entity}, stand={stand}: {file_path}"
                    )
                    continue
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

        if missing_items:
            message = "Старт прерван. Не найдены обязательные входные данные:\n" + "\n".join(
                f"- {item}" for item in missing_items
            )
            raise FileNotFoundError(message)

        self.logger.info("Найдено входных файлов: %s", len(descriptors))
        return descriptors

    def _parse_all_rows(
        self, files: list[FileDescriptor], conn: sqlite3.Connection
    ) -> tuple[dict[str, list[ParsedRow]], dict[str, Any]]:
        """Парсит CSV, формирует ключи/хэши строк и пишет данные в SQLite."""
        by_entity: dict[str, list[ParsedRow]] = defaultdict(list)
        cursor = conn.cursor()
        stats: dict[str, Any] = {
            "total_files": len(files),
            "db_new_files": 0,
            "db_skipped_files": 0,
            "processed_rows": 0,
            "rows_by_entity": defaultdict(int),
        }

        for descriptor in files:
            is_new_hash = self._is_new_file_hash(cursor, descriptor.file_hash)
            status = "NEW" if is_new_hash else "SKIPPED_DUPLICATE"
            if is_new_hash:
                stats["db_new_files"] += 1
            else:
                stats["db_skipped_files"] += 1
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
                    stats["processed_rows"] += 1
                    stats["rows_by_entity"][descriptor.entity] += 1
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

        return by_entity, stats

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

    def _merge_rows(
        self, parsed_rows: dict[str, list[ParsedRow]]
    ) -> tuple[dict[str, list[MergedRow]], dict[str, Any]]:
        """Объединяет данные по business_key + row_hash и формирует служебные признаки."""
        result: dict[str, list[MergedRow]] = {}
        stats: dict[str, Any] = {
            "merged_total": 0,
            "equal_all_total": 0,
            "different_total": 0,
            "same_key_different_values_total": 0,
            "by_entity": {},
        }

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

            # Для каждого business_key отмечаем строки, где найдено несколько разных значений.
            for item in merged_rows:
                has_diff_values = by_business_key_counts[item.business_key] > 1
                item.merged_data["same_key_diff_values_flag"] = "1" if has_diff_values else "0"
                item.merged_data["same_key_diff_values_note"] = (
                    "YES" if has_diff_values else "NO"
                )

            merged_rows.sort(key=lambda item: (item.business_key, item.row_hash))
            result[entity] = merged_rows
            entity_equal_all = sum(1 for item in merged_rows if item.is_equal_all)
            entity_diff = len(merged_rows) - entity_equal_all
            same_key_different = sum(
                1 for count in by_business_key_counts.values() if count > 1
            )
            stats["by_entity"][entity] = {
                "merged_rows": len(merged_rows),
                "equal_all_rows": entity_equal_all,
                "different_rows": entity_diff,
                "same_key_different_values": same_key_different,
            }
            stats["merged_total"] += len(merged_rows)
            stats["equal_all_total"] += entity_equal_all
            stats["different_total"] += entity_diff
            stats["same_key_different_values_total"] += same_key_different
        return result, stats

    def _export_excel(self, merged: dict[str, list[MergedRow]]) -> Path:
        """Создает Excel-файл: 1 лист на сущность + служебные колонки."""
        workbook = Workbook()
        workbook.remove(workbook.active)

        summary = workbook.create_sheet("SUMMARY")
        summary_headers = ["entity", "rows_total", "rows_equal_all", "rows_different"]
        summary.append(summary_headers)
        self._apply_sheet_layout(
            summary,
            self.config["excel"].get("summary_sheet", {"freeze_panes": "B2", "auto_filter_header": True}),
            len(summary_headers),
        )
        self._format_sheet(
            summary,
            base_headers=summary_headers,
            sheet_config=self.config["excel"].get("summary_sheet", {}),
            is_entity_sheet=False,
        )

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

            sheet_config = self.entities[entity].get(
                "excel_sheet", {"freeze_panes": "B2", "auto_filter_header": True}
            )
            self._apply_sheet_layout(sheet, sheet_config, len(base_headers))
            self._format_sheet(
                sheet,
                base_headers=base_headers,
                sheet_config=sheet_config,
                is_entity_sheet=True,
            )
            summary.append([entity, len(rows), equal_all, len(rows) - equal_all])

        file_name = datetime.now().strftime(
            self.config["excel"]["output_name_pattern"]
        )
        excel_path = self.paths["output_excel_dir"] / file_name
        workbook.save(excel_path)
        self.logger.info("Excel сформирован: %s", excel_path)
        return excel_path

    def _apply_sheet_layout(
        self, sheet: Any, sheet_config: dict[str, Any], header_columns_count: int
    ) -> None:
        """Применяет закрепление и автофильтр для листа Excel."""
        freeze_panes = sheet_config.get("freeze_panes")
        if freeze_panes:
            sheet.freeze_panes = freeze_panes

        if sheet_config.get("auto_filter_header", False) and header_columns_count > 0:
            sheet.auto_filter.ref = f"A1:{self._column_name(header_columns_count)}1"

    def _column_name(self, column_number: int) -> str:
        """Преобразует номер колонки в Excel-имя (1 -> A, 27 -> AA)."""
        result = ""
        current = column_number
        while current > 0:
            current, remainder = divmod(current - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def _format_sheet(
        self,
        sheet: Any,
        base_headers: list[str],
        sheet_config: dict[str, Any],
        is_entity_sheet: bool,
    ) -> None:
        """Применяет форматирование листа по параметрам из config.json."""
        formatting_defaults = self.config["excel"].get("formatting_defaults", {})
        sheet_formatting = dict(formatting_defaults)
        sheet_formatting.update(sheet_config.get("formatting", {}))

        max_column_width = sheet_formatting.get("max_column_width", 150)
        header_font = Font(
            bold=sheet_formatting.get("header_bold", True),
        )
        header_alignment = Alignment(
            horizontal=sheet_formatting.get("header_horizontal", "center"),
            vertical=sheet_formatting.get("header_vertical", "center"),
            wrap_text=sheet_formatting.get("wrap_text_all_cells", True),
        )
        data_alignment = Alignment(
            horizontal=sheet_formatting.get("data_horizontal", "left"),
            vertical=sheet_formatting.get("data_vertical", "center"),
            wrap_text=sheet_formatting.get("wrap_text_all_cells", True),
        )

        yellow_fill = PatternFill(
            fill_type="solid",
            fgColor=sheet_formatting.get("missing_prom_fill", "FFFDE9"),
        )
        red_fill = PatternFill(
            fill_type="solid",
            fgColor=sheet_formatting.get("same_key_diff_fill", "FDE9E9"),
        )

        # Заголовок: жирный + центрирование.
        for col_idx, _ in enumerate(base_headers, start=1):
            cell = sheet.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.alignment = header_alignment

        # Данные: выравнивание, перенос и цветовые правила.
        source_stands_index = (
            base_headers.index("source_stands") + 1 if "source_stands" in base_headers else None
        )
        same_key_diff_index = (
            base_headers.index("same_key_diff_values_flag") + 1
            if "same_key_diff_values_flag" in base_headers
            else None
        )

        for row_idx in range(2, sheet.max_row + 1):
            row_fill: PatternFill | None = None
            if is_entity_sheet and same_key_diff_index is not None:
                same_key_diff_value = str(sheet.cell(row=row_idx, column=same_key_diff_index).value or "")
                if same_key_diff_value == "1":
                    row_fill = red_fill
            if (
                is_entity_sheet
                and row_fill is None
                and source_stands_index is not None
            ):
                stands_value = str(sheet.cell(row=row_idx, column=source_stands_index).value or "")
                has_prom = "PROM" in stands_value
                has_ift_or_psi = ("IFT" in stands_value) or ("PSI" in stands_value)
                if (not has_prom) and has_ift_or_psi:
                    row_fill = yellow_fill

            for col_idx in range(1, len(base_headers) + 1):
                cell = sheet.cell(row=row_idx, column=col_idx)
                cell.alignment = data_alignment
                if row_fill is not None:
                    cell.fill = row_fill

        # Автоподбор ширины колонок с ограничением max_column_width.
        for col_idx in range(1, len(base_headers) + 1):
            max_len = 0
            for row_idx in range(1, sheet.max_row + 1):
                cell_value = sheet.cell(row=row_idx, column=col_idx).value
                if cell_value is None:
                    continue
                value_as_text = str(cell_value)
                candidate_len = max(len(line) for line in value_as_text.splitlines()) if value_as_text else 0
                if candidate_len > max_len:
                    max_len = candidate_len
            width = min(max(max_len + 2, 10), max_column_width)
            sheet.column_dimensions[self._column_name(col_idx)].width = width

    def _log_found_files_summary(self, files: list[FileDescriptor]) -> None:
        """Показывает в консоли, сколько и каких файлов найдено."""
        counts_by_entity: dict[str, int] = defaultdict(int)
        for item in files:
            counts_by_entity[item.entity] += 1
        self.logger.info("Старт пайплайна run_id=%s", self.run_id)
        for entity in sorted(counts_by_entity.keys()):
            self.logger.info("Найдено файлов %s: %s", entity, counts_by_entity[entity])

    def _log_parse_summary(self, parse_stats: dict[str, Any]) -> None:
        """Пишет статистику загрузки и обновления БД."""
        self.logger.info("Файлов обработано: %s", parse_stats["total_files"])
        self.logger.info(
            "Обновление БД: новых файлов=%s, совпали хэши (пропуск)=%s",
            parse_stats["db_new_files"],
            parse_stats["db_skipped_files"],
        )
        self.logger.info("Всего строк прочитано: %s", parse_stats["processed_rows"])

    def _log_merge_summary(self, merge_stats: dict[str, Any]) -> None:
        """Пишет статистику совпадений и разночтений между стендами."""
        self.logger.info(
            "Итог merged-строк: %s (совпали во всех стендах=%s, разночтения=%s)",
            merge_stats["merged_total"],
            merge_stats["equal_all_total"],
            merge_stats["different_total"],
        )
        self.logger.info(
            "Найдено ключей с разными значениями между стендами: %s",
            merge_stats["same_key_different_values_total"],
        )
        for entity in sorted(merge_stats["by_entity"].keys()):
            item = merge_stats["by_entity"][entity]
            self.logger.info(
                "%s -> merged=%s, equal_all=%s, different=%s, same_key_diff_values=%s",
                entity,
                item["merged_rows"],
                item["equal_all_rows"],
                item["different_rows"],
                item["same_key_different_values"],
            )

