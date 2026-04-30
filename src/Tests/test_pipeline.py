import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spod_exporter.models import ParsedRow
from spod_exporter.pipeline import SpodPipeline


def _build_pipeline() -> SpodPipeline:
    """Создает минимальный pipeline-объект для unit-тестов."""
    config = {
        "paths": {
            "input_root": "IN/SPOD",
            "output_excel_dir": "OUT/CSV",
            "output_db_dir": "OUT/DB",
            "log_dir": "log",
        },
        "stands": ["IFT", "PROM", "PSI"],
        "entities": {
            "CONTEST": {
                "file_names": {
                    "IFT": "contest_ift.csv",
                    "PROM": "contest_prom.csv",
                    "PSI": "contest_psi.csv",
                },
                "business_key": ["CONTEST_CODE"],
            },
            "GROUP": {
                "file_names": {
                    "IFT": "group_ift.csv",
                    "PROM": "group_prom.csv",
                    "PSI": "group_psi.csv",
                },
                "business_key": ["CONTEST_CODE", "GROUP_CODE", "GROUP_VALUE"],
            },
        },
        "sqlite": {"db_name": "test.db"},
        "merge": {
            "trim_values": False,
            "empty_to_null": False,
            "reference_row_stand": "PROM",
        },
        "excel": {
            "formatting_defaults": {"service_fields": []},
        },
    }
    logger = logging.getLogger("test_spod")
    return SpodPipeline(config=config, logger=logger)


class TestSpodPipeline(unittest.TestCase):
    """Проверки ключевых правил формирования ключей."""

    def test_business_key_for_group(self) -> None:
        pipeline = _build_pipeline()
        row = {"CONTEST_CODE": "C1", "GROUP_CODE": "BANK", "GROUP_VALUE": "*"}
        key = pipeline._build_business_key("GROUP", row)  # pylint: disable=protected-access
        self.assertEqual(key, "C1|BANK|*")

    def test_business_key_fallback_hash_when_empty(self) -> None:
        pipeline = _build_pipeline()
        row = {"CONTEST_CODE": ""}
        key = pipeline._build_business_key("CONTEST", row)  # pylint: disable=protected-access
        self.assertTrue(key.startswith("HASH:"))

    def test_merge_default_selects_row_by_stand_priority(self) -> None:
        """По ключу выбирается одна строка по приоритету стенда: PROM -> PSI -> IFT."""
        pipeline = _build_pipeline()
        business_key = "01|GROUPING|*"
        forced_hash = "same_hash_value"
        base_fields = {
            "CONTEST_CODE": "01",
            "GROUP_CODE": "GROUPING",
            "GROUP_VALUE": "*",
        }
        rows = [
            ParsedRow(
                stand="IFT",
                entity="GROUP",
                row_num=2,
                data={**base_fields, "ADD_CALC_CRITERION": "2", "ADD_CALC_CRITERION_2": "2"},
                business_key=business_key,
                row_hash=forced_hash,
            ),
            ParsedRow(
                stand="PROM",
                entity="GROUP",
                row_num=2,
                data={**base_fields, "ADD_CALC_CRITERION": "3", "ADD_CALC_CRITERION_2": "5"},
                business_key=business_key,
                row_hash=forced_hash,
            ),
            ParsedRow(
                stand="PSI",
                entity="GROUP",
                row_num=2,
                data={**base_fields, "ADD_CALC_CRITERION": "2", "ADD_CALC_CRITERION_2": "2"},
                business_key=business_key,
                row_hash=forced_hash,
            ),
        ]
        merged, counts, diff_rows = pipeline._merge_entity_default(  # pylint: disable=protected-access
            "GROUP", rows
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].merged_data["ADD_CALC_CRITERION"], "3")
        self.assertEqual(merged[0].merged_data["same_row_stands"], "PROM")
        self.assertEqual(merged[0].merged_data["same_key_diff_stands"], "PSI-IFT")
        self.assertEqual(counts[business_key], 3)
        self.assertEqual(len(diff_rows), 3)

    def test_source_stands_only_where_display_payload_matches(self) -> None:
        """source_stands: только стенды, где все поля совпадают с эталонной выводимой строкой (тот же business_key)."""
        pipeline = _build_pipeline()
        bk = "K1|G|*"
        display = {"CONTEST_CODE": "K1", "GROUP_CODE": "G", "GROUP_VALUE": "*", "X": "1"}
        rows = [
            ParsedRow(
                stand="PROM",
                entity="GROUP",
                row_num=2,
                data={"CONTEST_CODE": "K1", "GROUP_CODE": "G", "GROUP_VALUE": "*", "X": "1"},
                business_key=bk,
                row_hash="h",
            ),
            ParsedRow(
                stand="IFT",
                entity="GROUP",
                row_num=2,
                data={"CONTEST_CODE": "K1", "GROUP_CODE": "G", "GROUP_VALUE": "*", "X": "9"},
                business_key=bk,
                row_hash="h",
            ),
        ]
        stands = pipeline._collect_stands_matching_display_payload(bk, display, rows)  # pylint: disable=protected-access
        self.assertEqual(stands, ["PROM"])

    def test_rows_with_missing_optional_column_follow_priority(self) -> None:
        """Если выбран PROM, доп.колонка только в IFT не переносится в лист сущности."""
        pipeline = _build_pipeline()
        business_key = "C2"
        rows = [
            ParsedRow(
                stand="PROM",
                entity="CONTEST",
                row_num=2,
                data={"CONTEST_CODE": "C2", "NAME": "Contest 2"},
                business_key=business_key,
                row_hash="h1",
            ),
            ParsedRow(
                stand="IFT",
                entity="CONTEST",
                row_num=2,
                data={"CONTEST_CODE": "C2", "NAME": "Contest 2", "EXTRA_RULE": "X"},
                business_key=business_key,
                row_hash="h2",
            ),
        ]
        merged, _, _ = pipeline._merge_entity_default("CONTEST", rows)  # pylint: disable=protected-access
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].source_stands, "PROM")
        self.assertEqual(merged[0].merged_data.get("same_key_diff_stands"), "IFT")
        self.assertIsNone(merged[0].merged_data.get("EXTRA_RULE"))

