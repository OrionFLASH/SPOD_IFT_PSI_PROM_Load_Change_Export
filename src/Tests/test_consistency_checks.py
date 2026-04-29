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

    def test_unique_scope_and_non_empty_filters(self) -> None:
        entity = "E1"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {"entities": {}, "output": {}},
                "rules": [
                    {
                        "id": "u_scope",
                        "type": "unique",
                        "entity": entity,
                        "enabled": True,
                        "scope": "per_stand",
                        "key_columns": ["K"],
                        "unique_scope_conditions": [{"column": "T", "value": "A"}],
                        "unique_require_non_empty": ["X"],
                        "output": {},
                    }
                ],
            },
            "entities": {entity: {"business_key": ["K"]}},
        }
        rows = [
            ParsedRow("PROM", entity, 2, {"K": "1", "T": "A", "X": "ok"}, "1", "h1"),
            ParsedRow("PROM", entity, 3, {"K": "1", "T": "A", "X": "ok"}, "1", "h2"),
            ParsedRow("PROM", entity, 4, {"K": "1", "T": "B", "X": "ok"}, "1", "h3"),
            ParsedRow("PROM", entity, 5, {"K": "1", "T": "A", "X": ""}, "1", "h4"),
        ]
        res = execute_consistency_checks(
            config=config,
            stands=["PROM"],
            entities=config["entities"],
            parsed_by_entity={entity: rows},
            merged_by_entity={entity: [_merged_one(entity, "1", {"K": "1"})]},
            field_orders={entity: {"PROM": ["K", "T", "X"]}},
            logger=_logger(),
        )
        vals = [v for v in res.violations if v.rule_id == "u_scope"]
        self.assertEqual(len(vals), 1)

    def test_referential_with_row_filters(self) -> None:
        src, tgt = "SRC", "TGT"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {"entities": {}, "output": {}},
                "rules": [
                    {
                        "id": "rflt",
                        "type": "referential",
                        "entity": src,
                        "enabled": True,
                        "scope": "per_stand",
                        "source_column": "FK",
                        "target_entity": tgt,
                        "target_key_columns": ["ID"],
                        "src_row_conditions": [{"column": "FLAG", "value": "Y"}],
                        "ref_row_conditions": [{"column": "ACTIVE", "value": "1"}],
                        "output": {},
                    }
                ],
            },
            "entities": {src: {"business_key": ["FK"]}, tgt: {"business_key": ["ID"]}},
        }
        parsed = {
            src: [
                ParsedRow("PROM", src, 2, {"FK": "10", "FLAG": "Y"}, "10", "h1"),
                ParsedRow("PROM", src, 3, {"FK": "20", "FLAG": "N"}, "20", "h2"),
            ],
            tgt: [
                ParsedRow("PROM", tgt, 2, {"ID": "10", "ACTIVE": "1"}, "10", "h3"),
                ParsedRow("PROM", tgt, 3, {"ID": "20", "ACTIVE": "0"}, "20", "h4"),
            ],
        }
        res = execute_consistency_checks(
            config=config,
            stands=["PROM"],
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity={src: [_merged_one(src, "10", {"FK": "10"})]},
            field_orders={src: {"PROM": ["FK", "FLAG"]}, tgt: {"PROM": ["ID", "ACTIVE"]}},
            logger=_logger(),
        )
        self.assertFalse(any(v.business_key == "20" for v in res.violations))

    def test_cross_sheet_date_lte_today_with_ref_lookup(self) -> None:
        src, ref = "REPORT", "SCHEDULE"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {"entities": {}, "output": {}},
                "rules": [
                    {
                        "id": "dt_ref",
                        "type": "cross_sheet_date_lte_today",
                        "entity": src,
                        "enabled": True,
                        "scope": "per_stand",
                        "sheet_ref": "TOURNAMENT-SCHEDULE",
                        "column_src": "TOURNAMENT_CODE",
                        "column_ref": "TOURNAMENT_CODE",
                        "column_date_ref": "START_DT",
                        "date_format": "%Y-%m-%d",
                        "output": {},
                    }
                ],
            },
            "entities": {src: {"business_key": ["TOURNAMENT_CODE"]}, ref: {"business_key": ["TOURNAMENT_CODE"]}},
        }
        parsed = {
            src: [ParsedRow("PROM", src, 2, {"TOURNAMENT_CODE": "T1"}, "T1", "h1")],
            ref: [ParsedRow("PROM", ref, 2, {"TOURNAMENT_CODE": "T1", "START_DT": "2999-01-01"}, "T1", "h2")],
        }
        res = execute_consistency_checks(
            config=config,
            stands=["PROM"],
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity={src: [_merged_one(src, "T1", {"TOURNAMENT_CODE": "T1"})]},
            field_orders={src: {"PROM": ["TOURNAMENT_CODE"]}, ref: {"PROM": ["TOURNAMENT_CODE", "START_DT"]}},
            logger=_logger(),
        )
        self.assertTrue(any(v.rule_id == "dt_ref" for v in res.violations))

    def test_json_field_equals_column_with_must_not_equal_and_filters(self) -> None:
        entity = "REWARD"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {"entities": {}, "output": {}},
                "rules": [
                    {
                        "id": "jeq",
                        "type": "json_field_equals_column",
                        "entity": entity,
                        "enabled": True,
                        "scope": "per_stand",
                        "json_column": "J",
                        "json_path": "parentRewardCode",
                        "compare_column": "REWARD_CODE",
                        "filter_column": "REWARD_TYPE",
                        "filter_value": "BADGE",
                        "json_filter_key": "masterBadge",
                        "json_filter_value": "N",
                        "must_not_equal": True,
                        "output": {},
                    }
                ],
            },
            "entities": {entity: {"business_key": ["REWARD_CODE"]}},
        }
        row = {
            "J": '{"parentRewardCode":"R1","masterBadge":"N"}',
            "REWARD_CODE": "R1",
            "REWARD_TYPE": "BADGE",
        }
        parsed = {entity: [ParsedRow("PROM", entity, 2, row, "R1", "h1")]}
        res = execute_consistency_checks(
            config=config,
            stands=["PROM"],
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity={entity: [_merged_one(entity, "R1", row)]},
            field_orders={entity: {"PROM": list(row.keys())}},
            logger=_logger(),
        )
        self.assertTrue(any(v.rule_id == "jeq" for v in res.violations))

    def test_json_priority_unique_per_contest_link_full_mode(self) -> None:
        reward, link = "REWARD", "REWARD-LINK"
        config = {
            "consistency_checks": {
                "enabled": True,
                "fail_fast": False,
                "csv_columns_count": {"entities": {}, "output": {}},
                "rules": [
                    {
                        "id": "jpri",
                        "type": "json_priority_unique_per_contest_link",
                        "entity": reward,
                        "enabled": True,
                        "scope": "per_stand",
                        "json_column": "REWARD_ADD_DATA",
                        "json_path": "priority",
                        "reward_code_column": "REWARD_CODE",
                        "link_sheet": link,
                        "link_contest_column": "CONTEST_CODE",
                        "link_reward_column": "REWARD_CODE",
                        "output": {},
                    }
                ],
            },
            "entities": {reward: {"business_key": ["REWARD_CODE"]}, link: {"business_key": ["CONTEST_CODE", "REWARD_CODE"]}},
        }
        parsed = {
            reward: [
                ParsedRow("PROM", reward, 2, {"REWARD_CODE": "R1", "REWARD_ADD_DATA": '{"priority":"1"}'}, "R1", "h1"),
                ParsedRow("PROM", reward, 3, {"REWARD_CODE": "R2", "REWARD_ADD_DATA": '{"priority":"1"}'}, "R2", "h2"),
            ],
            link: [
                ParsedRow("PROM", link, 2, {"CONTEST_CODE": "C1", "REWARD_CODE": "R1"}, "C1|R1", "h3"),
                ParsedRow("PROM", link, 3, {"CONTEST_CODE": "C1", "REWARD_CODE": "R2"}, "C1|R2", "h4"),
            ],
        }
        res = execute_consistency_checks(
            config=config,
            stands=["PROM"],
            entities=config["entities"],
            parsed_by_entity=parsed,
            merged_by_entity={reward: [_merged_one(reward, "R1", {"REWARD_CODE": "R1"})]},
            field_orders={reward: {"PROM": ["REWARD_CODE", "REWARD_ADD_DATA"]}, link: {"PROM": ["CONTEST_CODE", "REWARD_CODE"]}},
            logger=_logger(),
        )
        self.assertTrue(any(v.rule_id == "jpri" for v in res.violations))


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
