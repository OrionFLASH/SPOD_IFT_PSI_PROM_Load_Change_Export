"""Unit-тесты проверок консистентности (consistency_checks)."""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spod_exporter.consistency_checks import (
    execute_consistency_checks,
    is_consistency_checks_enabled,
)
from spod_exporter.models import MergedRow, ParsedRow


def _logger() -> logging.Logger:
    log = logging.getLogger("test_consistency")
    log.setLevel(logging.WARNING)
    return log


class TestConsistencyChecks(unittest.TestCase):
    """Синтетические сценарии для типов правил."""

    def test_is_consistency_checks_enabled(self) -> None:
        self.assertFalse(is_consistency_checks_enabled({}))
        self.assertFalse(is_consistency_checks_enabled(None))
        self.assertTrue(is_consistency_checks_enabled({"enabled": True, "rules": []}))
        self.assertFalse(is_consistency_checks_enabled({"enabled": False, "rules": []}))
        self.assertTrue(is_consistency_checks_enabled({"rules": [], "csv_columns_count": {}}))

    def test_missing_enabled_key_still_runs_checks(self) -> None:
        """Без ключа enabled секция непустая — проверки должны выполняться (регрессия)."""
        entity = "E1"
        config = {
            "consistency_checks": {
                "fail_fast": False,
                "csv_columns_count": {
                    "entities": {entity: {"expected_columns": 1}},
                    "output": {},
                },
                "rules": [],
            },
            "entities": {entity: {"business_key": ["A"]}},
        }
        stands = ["PROM"]
        pr = ParsedRow("PROM", entity, 2, {"A": "1", "B": "2"}, "1", "h1")
        parsed = {entity: [pr]}
        merged = {entity: [_merged_one(entity, "1", {"A": "1", "B": "2"})]}
        field_orders = {entity: {"PROM": ["A", "B"]}}
        res = execute_consistency_checks(
            config=config,
            stands=stands,
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity=merged,
            field_orders=field_orders,
            logger=_logger(),
        )
        self.assertGreater(len(res.violations), 0)

    def test_csv_columns_count_mismatch_header(self) -> None:
        entity = "E1"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {
                    "entities": {entity: {"expected_columns": 2}},
                    "output": {},
                },
                "rules": [],
            },
            "entities": {entity: {"business_key": ["A"]}},
        }
        stands = ["PROM"]
        pr = ParsedRow(
            stand="PROM",
            entity=entity,
            row_num=2,
            data={"A": "1", "B": "2"},
            business_key="1",
            row_hash="h1",
        )
        parsed = {entity: [pr]}
        merged = {entity: [_merged_one(entity, "1", {"A": "1", "B": "2"})]}
        field_orders = {entity: {"PROM": ["A", "B", "EXTRA"]}}
        res = execute_consistency_checks(
            config=config,
            stands=stands,
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity=merged,
            field_orders=field_orders,
            logger=_logger(),
        )
        types = {v.rule_type for v in res.violations}
        self.assertIn("csv_columns_count", types)

    def test_unique_per_stand_duplicate(self) -> None:
        entity = "E1"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {"entities": {}, "output": {}},
                "rules": [
                    {
                        "id": "u1",
                        "type": "unique",
                        "entity": entity,
                        "enabled": True,
                        "scope": "per_stand",
                        "key_columns": ["K"],
                        "output": {"column_suffix_per_stand": "CC_U_S"},
                    }
                ],
            },
            "entities": {entity: {"business_key": ["K"]}},
        }
        stands = ["PROM"]
        rows = [
            ParsedRow("PROM", entity, 2, {"K": "x"}, "x", "h1"),
            ParsedRow("PROM", entity, 3, {"K": "x"}, "x", "h2"),
        ]
        parsed = {entity: rows}
        merged = {entity: [_merged_one(entity, "x", {"K": "x"})]}
        field_orders = {entity: {"PROM": ["K"]}}
        res = execute_consistency_checks(
            config=config,
            stands=stands,
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity=merged,
            field_orders=field_orders,
            logger=_logger(),
        )
        self.assertTrue(any(v.rule_type == "unique" and v.scope == "per_stand" for v in res.violations))

    def test_field_format_decimal(self) -> None:
        entity = "E1"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {"entities": {}, "output": {}},
                "rules": [
                    {
                        "id": "fmt1",
                        "type": "field_format",
                        "entity": entity,
                        "enabled": True,
                        "scope": "per_stand",
                        "column": "N",
                        "format_type": "decimal",
                        "params": {},
                        "output": {},
                    }
                ],
            },
            "entities": {entity: {"business_key": ["N"]}},
        }
        stands = ["PROM"]
        ok_row = ParsedRow("PROM", entity, 2, {"N": "3.14"}, "3.14", "h1")
        bad_row = ParsedRow("PROM", entity, 3, {"N": "x"}, "x", "h2")
        parsed = {entity: [ok_row, bad_row]}
        merged = {
            entity: [
                _merged_one(entity, "3.14", {"N": "3.14"}),
                _merged_one(entity, "x", {"N": "x"}),
            ]
        }
        field_orders = {entity: {"PROM": ["N"]}}
        res = execute_consistency_checks(
            config=config,
            stands=stands,
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity=merged,
            field_orders=field_orders,
            logger=_logger(),
        )
        self.assertTrue(any("decimal" in v.message for v in res.violations))

    def test_referential_missing_target(self) -> None:
        entity_src = "SRC"
        entity_tgt = "TGT"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {"entities": {}, "output": {}},
                "rules": [
                    {
                        "id": "r1",
                        "type": "referential",
                        "entity": entity_src,
                        "enabled": True,
                        "scope": "per_stand",
                        "source_column": "FK",
                        "target_entity": entity_tgt,
                        "target_key_columns": ["ID"],
                        "output": {},
                    }
                ],
            },
            "entities": {
                entity_src: {"business_key": ["FK"]},
                entity_tgt: {"business_key": ["ID"]},
            },
        }
        stands = ["PROM"]
        parsed = {
            entity_src: [ParsedRow("PROM", entity_src, 2, {"FK": "99"}, "99", "h1")],
            entity_tgt: [ParsedRow("PROM", entity_tgt, 2, {"ID": "1"}, "1", "h2")],
        }
        merged = {entity_src: [_merged_one(entity_src, "99", {"FK": "99"})]}
        field_orders = {
            entity_src: {"PROM": ["FK"]},
            entity_tgt: {"PROM": ["ID"]},
        }
        res = execute_consistency_checks(
            config=config,
            stands=stands,
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity=merged,
            field_orders=field_orders,
            logger=_logger(),
        )
        self.assertTrue(any(v.rule_type == "referential" for v in res.violations))

    def test_fail_fast_raises(self) -> None:
        entity = "E1"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": True,
                "csv_columns_count": {
                    "entities": {entity: {"expected_columns": 1}},
                    "output": {},
                },
                "rules": [],
            },
            "entities": {entity: {"business_key": ["A", "B"]}},
        }
        stands = ["PROM"]
        pr = ParsedRow("PROM", entity, 2, {"A": "1", "B": "2"}, "1|2", "h1")
        parsed = {entity: [pr]}
        merged = {entity: [_merged_one(entity, "1|2", {"A": "1", "B": "2"})]}
        field_orders = {entity: {"PROM": ["A", "B"]}}
        with self.assertRaises(ValueError) as ctx:
            execute_consistency_checks(
                config=config,
                stands=stands,
                entities=config["entities"],
                parsed_by_entity=parsed,
                merged_by_entity=merged,
                field_orders=field_orders,
                logger=_logger(),
            )
        self.assertIn("fail_fast", str(ctx.exception))

    def test_unique_per_stand_violation_matches_pipeline_business_key_with_spaces(self) -> None:
        """Ключ сравнивается со strip; business_key нарушения совпадает с ParsedRow (инъекция в merged)."""
        entity = "G"
        key_cols = ["CONTEST_CODE", "GROUP_CODE", "GROUP_VALUE"]
        row1 = {"CONTEST_CODE": " 01 ", "GROUP_CODE": "X", "GROUP_VALUE": "*", "TAIL": "1"}
        row2 = {"CONTEST_CODE": " 01 ", "GROUP_CODE": "X", "GROUP_VALUE": "*", "TAIL": "2"}
        bk = "|".join(row1.get(c, "") for c in key_cols)
        pr1 = ParsedRow("IFT", entity, 2, row1, bk, "h1")
        pr2 = ParsedRow("IFT", entity, 3, row2, bk, "h2")
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {"entities": {}, "output": {}},
                "rules": [
                    {
                        "id": "u_sp",
                        "type": "unique",
                        "entity": entity,
                        "enabled": True,
                        "scope": "per_stand",
                        "key_columns": key_cols,
                        "output": {"column_suffix_per_stand": "CC_U_S"},
                    }
                ],
            },
            "entities": {entity: {"business_key": key_cols}},
        }
        parsed = {entity: [pr1, pr2]}
        merged = {entity: [_merged_one(entity, bk, dict(row1))]}
        field_orders = {entity: {"IFT": list(row1.keys())}}
        res = execute_consistency_checks(
            config=config,
            stands=["IFT"],
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity=merged,
            field_orders=field_orders,
            logger=_logger(),
        )
        vuniq = [v for v in res.violations if v.rule_id == "u_sp" and v.scope == "per_stand"]
        self.assertEqual(len(vuniq), 1)
        self.assertEqual(vuniq[0].business_key, bk)
        self.assertIn("CC_U_S", merged[entity][0].merged_data)
        self.assertNotEqual(merged[entity][0].merged_data.get("CC_U_S"), "OK")


def _merged_one(entity: str, bk: str, data: dict[str, str]) -> MergedRow:
    return MergedRow(
        entity=entity,
        business_key=bk,
        row_hash="rh",
        source_stands="PROM",
        source_count=1,
        is_equal_all=True,
        diff_group_key="",
        merged_data=dict(data),
    )


if __name__ == "__main__":
    unittest.main()
