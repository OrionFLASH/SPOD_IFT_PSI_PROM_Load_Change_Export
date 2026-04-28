"""
Интеграция: дубликаты GROUP по реальным CSV (CONTEST 01_2026-0_05-2_4).
Пропускается, если в репозитории нет IN/SPOD.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spod_exporter.config import load_config
from spod_exporter.consistency_checks import execute_consistency_checks
from spod_exporter.pipeline import SpodPipeline

IFT_GROUP = Path(__file__).resolve().parents[2] / "IN" / "SPOD" / "IFT" / "GROUP (PROM) 17-04 v0.csv"


class TestConsistencyRealGroupDuplicates(unittest.TestCase):
    """Проверка uniq_group_key на данных с дублем 01_2026-0_05-2_4|GROUPING|*."""

    @unittest.skipUnless(IFT_GROUP.is_file(), f"нет файла {IFT_GROUP}")
    def test_violations_for_contest_01_2026_0_05_2_4(self) -> None:
        cfg_path = Path(__file__).resolve().parents[2] / "config.json"
        config = load_config(cfg_path)
        log = logging.getLogger("test_real_group")
        log.setLevel(logging.WARNING)
        p = SpodPipeline(config, log)
        conn = sqlite3.connect(":memory:")
        p._init_db(conn)
        files = p._scan_files()
        parsed, _ = p._parse_all_rows(files, conn)
        merged, _ = p._merge_rows(parsed)
        fo = {e: dict(p.entity_field_orders[e]) for e in p.entities}
        res = execute_consistency_checks(
            config=config,
            stands=list(p.stands),
            entities=p.entities,
            parsed_by_entity=parsed,
            merged_by_entity=merged,
            field_orders=fo,
            logger=log,
        )
        target_bk = "01_2026-0_05-2_4|GROUPING|*"
        ug = [v for v in res.violations if v.rule_id == "uniq_group_key" and v.business_key == target_bk]
        self.assertGreaterEqual(
            len(ug),
            2,
            "ожидаются per_stand (IFT/PSI) и/или merged; проверьте enabled у uniq_group_key в config.json",
        )
        merged_m = merged["GROUP"]
        rows_target = [m for m in merged_m if m.business_key == target_bk]
        self.assertEqual(len(rows_target), 2, "две merged-строки с одним business_key при разном сыром")
        for m in rows_target:
            self.assertIn("CC_UNIQ_GROUP_S", m.merged_data)
            self.assertIn("CC_UNIQ_GROUP_M", m.merged_data)
            self.assertNotEqual(m.merged_data.get("CC_UNIQ_GROUP_M"), "OK")
        conn.close()


if __name__ == "__main__":
    unittest.main()
