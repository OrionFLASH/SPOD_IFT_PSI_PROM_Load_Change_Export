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
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

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
        self.entity_field_orders: dict[str, dict[str, list[str]]] = defaultdict(dict)
        self.entity_extra_fields: dict[str, set[str]] = defaultdict(set)

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
                if reader.fieldnames is not None:
                    self.entity_field_orders[descriptor.entity][descriptor.stand] = list(
                        reader.fieldnames
                    )
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
            optional_cfg = self.entities[entity].get("optional_fields", {})
            if optional_cfg.get("enabled", False):
                merged_rows, by_business_key_counts = self._merge_entity_with_optional_fields(
                    entity=entity,
                    rows=rows,
                    reference_stand=optional_cfg.get("reference_stand", "PROM"),
                )
            else:
                merged_rows, by_business_key_counts = self._merge_entity_default(entity, rows)

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

    def _merge_entity_default(
        self, entity: str, rows: list[ParsedRow]
    ) -> tuple[list[MergedRow], dict[str, int]]:
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
        return merged_rows, by_business_key_counts

    def _merge_entity_with_optional_fields(
        self, entity: str, rows: list[ParsedRow], reference_stand: str
    ) -> tuple[list[MergedRow], dict[str, int]]:
        field_orders = self.entity_field_orders.get(entity, {})
        reference_fields = set(field_orders.get(reference_stand, []))
        all_fields = set()
        for stand_fields in field_orders.values():
            all_fields.update(stand_fields)
        extra_fields = all_fields - reference_fields
        self.entity_extra_fields[entity] = set(extra_fields)
        non_extra_fields = [field for field in all_fields if field not in extra_fields]

        by_key_rows: dict[str, list[ParsedRow]] = defaultdict(list)
        for row in rows:
            by_key_rows[row.business_key].append(row)

        merged_rows: list[MergedRow] = []
        by_business_key_counts: dict[str, int] = defaultdict(int)
        stand_priority = [reference_stand] + [stand for stand in self.stands if stand != reference_stand]
        stand_rank = {name: idx for idx, name in enumerate(stand_priority)}

        for business_key, key_rows in by_key_rows.items():
            sorted_rows = sorted(key_rows, key=lambda item: stand_rank.get(item.stand, 999))
            clusters: list[list[ParsedRow]] = []
            for candidate in sorted_rows:
                placed = False
                for cluster in clusters:
                    if self._row_compatible_with_cluster(
                        candidate=candidate,
                        cluster=cluster,
                        non_extra_fields=non_extra_fields,
                        extra_fields=extra_fields,
                    ):
                        cluster.append(candidate)
                        placed = True
                        break
                if not placed:
                    clusters.append([candidate])

            for cluster_rows in clusters:
                by_business_key_counts[business_key] += 1
                stands = sorted({row.stand for row in cluster_rows})
                source_stands = "-".join(stands)
                source_count = len(stands)
                diff_group_key = f"{entity}:{business_key}:{by_business_key_counts[business_key]}"
                base_data = dict(cluster_rows[0].data)
                base_data["source_stands"] = source_stands
                base_data["source_count"] = str(source_count)
                base_data["is_equal_all"] = "1" if source_count == len(self.stands) else "0"
                base_data["diff_group_key"] = diff_group_key
                merged_rows.append(
                    MergedRow(
                        entity=entity,
                        business_key=business_key,
                        row_hash=self._hash_json(base_data),
                        source_stands=source_stands,
                        source_count=source_count,
                        is_equal_all=source_count == len(self.stands),
                        diff_group_key=diff_group_key,
                        merged_data=base_data,
                    )
                )
        return merged_rows, by_business_key_counts

    def _row_compatible_with_cluster(
        self,
        candidate: ParsedRow,
        cluster: list[ParsedRow],
        non_extra_fields: list[str],
        extra_fields: set[str],
    ) -> bool:
        for row in cluster:
            if not self._rows_compatible(
                left=row.data,
                right=candidate.data,
                non_extra_fields=non_extra_fields,
                extra_fields=extra_fields,
            ):
                return False
        return True

    def _rows_compatible(
        self,
        left: dict[str, str],
        right: dict[str, str],
        non_extra_fields: list[str],
        extra_fields: set[str],
    ) -> bool:
        for field in non_extra_fields:
            if left.get(field, "") != right.get(field, ""):
                return False
        for field in extra_fields:
            if field in left and field in right and left.get(field, "") != right.get(field, ""):
                return False
        return True

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
            entity_name=None,
        )

        for entity in self.entities.keys():
            sheet = workbook.create_sheet(entity)
            rows = merged.get(entity, [])
            base_headers = self._build_entity_headers(entity, rows)
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
                entity_name=entity,
            )
            summary.append([entity, len(rows), equal_all, len(rows) - equal_all])

        output_prefix = self.config["excel"]["output_name_prefix"]
        timestamp_format = self.config["excel"]["output_timestamp_format"]
        timestamp_value = datetime.now().strftime(timestamp_format)
        file_name = f"{output_prefix}_{timestamp_value}.xlsx"
        excel_path = self.paths["output_excel_dir"] / file_name
        workbook.save(excel_path)
        self.logger.info("Excel сформирован: %s", excel_path)
        return excel_path

    def _build_entity_headers(self, entity: str, rows: list[MergedRow]) -> list[str]:
        """Строит порядок колонок: эталон PROM + доп.поля в местах появления."""
        service_fields_order = self.config["excel"]["formatting_defaults"].get("service_fields", [])
        present_service = [field for field in service_fields_order if any(field in row.merged_data for row in rows)]

        field_orders = self.entity_field_orders.get(entity, {})
        reference_stand = self.entities[entity].get("optional_fields", {}).get("reference_stand", "PROM")
        reference_headers = list(field_orders.get(reference_stand, []))
        combined_headers = list(reference_headers)

        for stand in self.stands:
            stand_headers = field_orders.get(stand, [])
            for index, field in enumerate(stand_headers):
                if field in combined_headers:
                    continue
                insert_index = min(index, len(combined_headers))
                combined_headers.insert(insert_index, field)

        if not combined_headers and rows:
            combined_headers = [key for key in rows[0].merged_data.keys() if key not in service_fields_order]

        for service_field in present_service:
            if service_field not in combined_headers:
                combined_headers.append(service_field)
        return combined_headers

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
        entity_name: str | None = None,
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
        key_header_fill = PatternFill(
            fill_type="solid",
            fgColor=sheet_formatting.get("key_header_fill", "FCE4B2"),
        )
        extra_header_fill = PatternFill(
            fill_type="solid",
            fgColor=sheet_formatting.get("extra_header_fill", "E2F0D9"),
        )
        fill_priority: list[str] = sheet_formatting.get(
            "fill_priority", ["same_key_diff", "missing_prom"]
        )
        borders_cfg = sheet_formatting.get("borders", {})
        border_color = borders_cfg.get("color", "666666")
        horizontal_side = Side(
            style=borders_cfg.get("horizontal_style", "dashed"),
            color=border_color,
        )
        vertical_side = Side(
            style=borders_cfg.get("vertical_style", "dotted"),
            color=border_color,
        )
        header_separator_side = Side(
            style=borders_cfg.get("header_separator_style", "double"),
            color=border_color,
        )
        service_separator_side = Side(
            style=borders_cfg.get("service_separator_style", "thick"),
            color=border_color,
        )
        service_separator_edge = borders_cfg.get("service_separator_side", "top")
        service_fields = set(sheet_formatting.get("service_fields", []))
        service_column_indexes = {
            idx + 1 for idx, name in enumerate(base_headers) if name in service_fields
        }
        first_service_column_index = (
            min(service_column_indexes) if service_column_indexes else None
        )

        # Заголовок: жирный + центрирование.
        key_fields: set[str] = set()
        extra_fields: set[str] = set()
        if entity_name and entity_name in self.entities:
            key_fields = set(self.entities[entity_name].get("business_key", []))
            extra_fields = self.entity_extra_fields.get(entity_name, set())
        for col_idx, _ in enumerate(base_headers, start=1):
            cell = sheet.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.alignment = header_alignment
            cell.border = Border(bottom=header_separator_side)
            if base_headers[col_idx - 1] in key_fields:
                cell.fill = key_header_fill
            elif base_headers[col_idx - 1] in extra_fields:
                cell.fill = extra_header_fill

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
            row_fill: PatternFill | None = self._resolve_row_fill(
                row_idx=row_idx,
                sheet=sheet,
                is_entity_sheet=is_entity_sheet,
                source_stands_index=source_stands_index,
                same_key_diff_index=same_key_diff_index,
                fill_priority=fill_priority,
                yellow_fill=yellow_fill,
                red_fill=red_fill,
            )

            for col_idx in range(1, len(base_headers) + 1):
                cell = sheet.cell(row=row_idx, column=col_idx)
                cell.alignment = data_alignment
                cell.border = Border(
                    left=vertical_side,
                    right=vertical_side,
                    top=horizontal_side,
                    bottom=horizontal_side,
                )
                if row_fill is not None:
                    cell.fill = row_fill

                if first_service_column_index is not None and col_idx == first_service_column_index:
                    cell.border = self._with_border_side(
                        border=cell.border,
                        side_name=service_separator_edge,
                        side_value=service_separator_side,
                    )

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

    def _with_border_side(
        self, border: Border, side_name: str, side_value: Side
    ) -> Border:
        """Возвращает Border с замененной одной стороной."""
        sides = {
            "left": border.left,
            "right": border.right,
            "top": border.top,
            "bottom": border.bottom,
        }
        if side_name not in sides:
            return border
        sides[side_name] = side_value
        return Border(
            left=sides["left"],
            right=sides["right"],
            top=sides["top"],
            bottom=sides["bottom"],
        )

    def _resolve_row_fill(
        self,
        row_idx: int,
        sheet: Any,
        is_entity_sheet: bool,
        source_stands_index: int | None,
        same_key_diff_index: int | None,
        fill_priority: list[str],
        yellow_fill: PatternFill,
        red_fill: PatternFill,
    ) -> PatternFill | None:
        """Возвращает цвет строки в порядке приоритета из config."""
        if not is_entity_sheet:
            return None

        conditions: dict[str, bool] = {}
        if same_key_diff_index is not None:
            same_key_diff_value = str(sheet.cell(row=row_idx, column=same_key_diff_index).value or "")
            conditions["same_key_diff"] = same_key_diff_value == "1"
        if source_stands_index is not None:
            stands_value = str(sheet.cell(row=row_idx, column=source_stands_index).value or "")
            has_prom = "PROM" in stands_value
            has_ift_or_psi = ("IFT" in stands_value) or ("PSI" in stands_value)
            conditions["missing_prom"] = (not has_prom) and has_ift_or_psi

        for rule_name in fill_priority:
            if conditions.get(rule_name, False):
                if rule_name == "same_key_diff":
                    return red_fill
                if rule_name == "missing_prom":
                    return yellow_fill
        return None

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

