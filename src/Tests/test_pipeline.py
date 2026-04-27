import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spod_exporter.pipeline import SpodPipeline


def _build_pipeline() -> SpodPipeline:
    """Создает минимальный pipeline-объект для unit-тестов."""
    config = {
        "paths": {
            "input_root": "IN/SPOD",
            "output_excel_dir": "OUT/CSV",
            "output_db_dir": "OUT/DB",
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
        "excel": {"output_name_pattern": "test_%Y%m%d.xlsx"},
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

