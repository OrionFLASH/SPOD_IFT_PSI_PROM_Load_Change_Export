"""
Проверки консистентности по аналогии с SPOD_PARCE_LOAD (consistency_checks в config.json).
Работа без pandas: сырые dict из CSV, ParsedRow, MergedRow.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .models import MergedRow, ParsedRow


def _sheet_alias_to_entity_name(name: str) -> str:
    s = str(name or "").strip()
    if not s:
        return ""
    aliases = {
        "CONTEST-DATA": "CONTEST",
        "TOURNAMENT-SCHEDULE": "SCHEDULE",
        "TOURNAMENT-SHEDULE": "SCHEDULE",
    }
    return aliases.get(s, s)


def _to_strftime_format(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return s
    s = s.replace("YYYY", "%Y").replace("MM", "%m").replace("DD", "%d")
    return s


def _normalize_rule_schema(rule: dict[str, Any]) -> dict[str, Any]:
    """
    Нормализует SPOD-совместимый формат rules к внутреннему формату текущего проекта.
    Поддерживает алиасы:
    - sheet/sheet_src/sheet_ref -> entity/target_entity
    - column_src/column_ref, columns_src/columns_ref
    - json_key -> json_path; column_compare -> compare_column
    - field+format -> column+format_type+params
    """
    r = dict(rule)
    rtype = str(r.get("type", "")).strip()

    if "entity" not in r and "sheet" in r:
        r["entity"] = _sheet_alias_to_entity_name(r.get("sheet"))
    if "entity" not in r and "sheet_src" in r:
        r["entity"] = _sheet_alias_to_entity_name(r.get("sheet_src"))

    if rtype in {"referential", "referential_composite"}:
        if "target_entity" not in r and "sheet_ref" in r:
            r["target_entity"] = _sheet_alias_to_entity_name(r.get("sheet_ref"))
        if "source_column" not in r and "column_src" in r:
            r["source_column"] = r.get("column_src")
        if "target_key_columns" not in r and "column_ref" in r:
            r["target_key_columns"] = [r.get("column_ref")]
        if "source_columns" not in r and "columns_src" in r:
            r["source_columns"] = list(r.get("columns_src") or [])
        if "target_key_columns" not in r and "columns_ref" in r:
            r["target_key_columns"] = list(r.get("columns_ref") or [])

    if rtype == "json_field_equals_column":
        if "json_path" not in r and "json_key" in r:
            r["json_path"] = r.get("json_key")
        if "compare_column" not in r and "column_compare" in r:
            r["compare_column"] = r.get("column_compare")

    if rtype == "json_field_in_column":
        if "json_path" not in r and "json_key" in r:
            r["json_path"] = r.get("json_key")

    if rtype == "json_priority_unique_per_contest_link":
        if "json_path" not in r and "json_key" in r:
            r["json_path"] = r.get("json_key")
        if "link_sheet" in r:
            r["link_sheet"] = _sheet_alias_to_entity_name(r.get("link_sheet"))

    if rtype == "cross_sheet_date_lte_today":
        if "sheet_ref" in r:
            r["sheet_ref"] = _sheet_alias_to_entity_name(r.get("sheet_ref"))
        if "date_format" in r:
            r["date_format"] = _to_strftime_format(str(r.get("date_format", "")))

    if rtype == "field_format":
        # Старый формат SPOD: field + format{}
        if "column" not in r and "field" in r:
            r["column"] = r.get("field")
        fmt_obj = r.get("format")
        if isinstance(fmt_obj, dict):
            if "format_type" not in r:
                r["format_type"] = str(fmt_obj.get("type", "")).strip().lower()
            params = dict(r.get("params") or {})
            if "date_format" in fmt_obj:
                params["strftime"] = _to_strftime_format(str(fmt_obj.get("date_format")))
            if "length" in fmt_obj:
                params["length"] = int(fmt_obj.get("length"))
            if "decimal_places" in fmt_obj:
                # Для decimal в текущей реализации проверяем только тип числа, без точной шкалы.
                params["decimal_places"] = int(fmt_obj.get("decimal_places"))
            if "special_values" in fmt_obj:
                params["special_values"] = list(fmt_obj.get("special_values") or [])
            if "allow_empty" in fmt_obj:
                params["allow_empty"] = bool(fmt_obj.get("allow_empty"))
            if params:
                r["params"] = params

    return r


def _normalize_cell_for_compare(value: Any) -> str:
    return str("" if value is None else value).strip()


def _cell_is_empty_for_scope(value: Any) -> bool:
    v = _normalize_cell_for_compare(value).lower()
    return v in {"", "-", "none", "null", "nan"}


def _normalize_rule_conditions(
    rule: dict[str, Any],
    base_name: str,
) -> list[dict[str, str]]:
    """
    Нормализует условия фильтрации строк.
    Поддерживает:
    - <base_name>_conditions: [{column, value}] или [{column, op, value}]
    - <base_name>_column + <base_name>_value (legacy)
    """
    out: list[dict[str, str]] = []
    raw = rule.get(f"{base_name}_conditions")
    if isinstance(raw, list):
        for it in raw:
            if not isinstance(it, dict):
                continue
            col = str(it.get("column", "")).strip()
            if not col:
                continue
            op = str(it.get("op", "=")).strip().lower()
            val = str(it.get("value", "")).strip()
            out.append({"column": col, "op": op, "value": val})
        if out:
            return out
    legacy_col = str(rule.get(f"{base_name}_column", "")).strip()
    if legacy_col:
        out.append(
            {
                "column": legacy_col,
                "op": "=",
                "value": str(rule.get(f"{base_name}_value", "")).strip(),
            }
        )
    return out


def _row_matches_conditions(
    data: dict[str, str],
    conditions: list[dict[str, str]],
    mode: str = "all",
) -> bool:
    if not conditions:
        return True
    checks: list[bool] = []
    for c in conditions:
        col = c["column"]
        op = c.get("op", "=").lower()
        left = _normalize_cell_for_compare(data.get(col, ""))
        right = _normalize_cell_for_compare(c.get("value", ""))
        if op in {"=", "==", "eq"}:
            checks.append(left == right)
        elif op in {"!=", "<>", "ne"}:
            checks.append(left != right)
        else:
            checks.append(left == right)
    return any(checks) if mode in {"any", "or", "или"} else all(checks)


def is_consistency_checks_enabled(cc: Any) -> bool:
    """
    Возвращает True, если блок consistency_checks в конфиге задан непустым dict
    и явно не отключён (enabled=false).

    Если ключа enabled нет, считаем проверки **включёнными** — иначе при наличии
    только rules/csv_columns_count проверки молча не запускались бы.
    """
    if not isinstance(cc, dict) or len(cc) == 0:
        return False
    if "enabled" not in cc:
        return True
    v = cc["enabled"]
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on", "да")
    return bool(v)


@dataclass
class ConsistencyViolation:
    """Одна запись отклонения для листа CONSISTENCY и логов."""

    rule_id: str
    rule_type: str
    scope: str
    entity: str
    stand: str | None
    row_num: int | None
    business_key: str | None
    message: str
    severity: str = "warning"  # warning | error


def _scopes_for_rule(rule: dict[str, Any]) -> list[str]:
    """Возвращает список областей запуска: per_stand, merged."""
    raw = str(rule.get("scope", "per_stand")).strip().lower()
    if raw == "both":
        return ["per_stand", "merged"]
    if raw == "merged":
        return ["merged"]
    return ["per_stand"]


def _key_tuple_from_row(row: dict[str, str], key_columns: list[str]) -> tuple[str, ...]:
    return tuple(str(row.get(c, "")).strip() for c in key_columns)


def _canonical_business_key_for_key_tuple(
    parsed_rows: list[ParsedRow],
    key_columns: list[str],
    kt: tuple[str, ...],
) -> str | None:
    """
    Возвращает business_key первой строки с данным составным ключом.
    Нужен, чтобы нарушение unique совпадало с ключом merged-строк (в пайплайне bk без .strip() по полям).
    """
    for pr in parsed_rows:
        if _key_tuple_from_row(pr.data, key_columns) == kt:
            return pr.business_key
    return None


def _build_target_key_set(
    rows: list[ParsedRow],
    key_columns: list[str],
) -> set[tuple[str, ...]]:
    return {_key_tuple_from_row(pr.data, key_columns) for pr in rows}


def run_csv_columns_count_for_stand(
    rule: dict[str, Any],
    entity: str,
    stand: str,
    headers: list[str],
    parsed_rows: list[ParsedRow],
    expected_from_config: int | None,
    max_headers_across_stands: int,
) -> list[ConsistencyViolation]:
    """
    Число колонок: заголовок и каждая строка должны иметь одинаковую размерность.
    Ожидание: из конфига или 0 = max(число колонок заголовка) по стендам для сущности.
    """
    violations: list[ConsistencyViolation] = []
    rid = str(rule.get("id", "csv_columns_count"))
    if expected_from_config == 0 or expected_from_config is None:
        expected = max_headers_across_stands
    else:
        expected = int(expected_from_config)
    header_len = len(headers)
    if header_len != expected:
        violations.append(
            ConsistencyViolation(
                rule_id=rid,
                rule_type="csv_columns_count",
                scope="per_stand",
                entity=entity,
                stand=stand,
                row_num=None,
                business_key=None,
                message=f"в заголовке полей={header_len}, ожидалось={expected}",
                severity="error",
            )
        )
        return violations
    for pr in parsed_rows:
        n = len(pr.data)
        if n != header_len:
            violations.append(
                ConsistencyViolation(
                    rule_id=rid,
                    rule_type="csv_columns_count",
                    scope="per_stand",
                    entity=entity,
                    stand=stand,
                    row_num=pr.row_num,
                    business_key=pr.business_key,
                    message=(
                        f"в строке полей={n}, не совпадает с заголовком ({header_len})"
                    ),
                    severity="error",
                )
            )
    return violations


def run_unique_per_stand(
    rule: dict[str, Any],
    entity: str,
    stand: str,
    parsed_rows: list[ParsedRow],
    key_columns: list[str],
) -> list[ConsistencyViolation]:
    scope_mode = str(rule.get("unique_scope_mode", "all")).strip().lower()
    scope_conditions = _normalize_rule_conditions(rule, "unique_scope")
    require_non_empty = [
        str(c).strip() for c in (rule.get("unique_require_non_empty") or []) if str(c).strip()
    ]
    counts: dict[tuple[str, ...], list[int]] = {}
    for pr in parsed_rows:
        if not _row_matches_conditions(pr.data, scope_conditions, scope_mode):
            continue
        if require_non_empty and any(_cell_is_empty_for_scope(pr.data.get(c, "")) for c in require_non_empty):
            continue
        kt = _key_tuple_from_row(pr.data, key_columns)
        counts.setdefault(kt, []).append(pr.row_num)
    rid = str(rule.get("id", "unique"))
    out: list[ConsistencyViolation] = []
    for kt, lines in counts.items():
        if len(lines) <= 1:
            continue
        bk_viol = _canonical_business_key_for_key_tuple(parsed_rows, key_columns, kt)
        if bk_viol is None:
            bk_viol = "|".join(kt)
        out.append(
            ConsistencyViolation(
                rule_id=rid,
                rule_type="unique",
                scope="per_stand",
                entity=entity,
                stand=stand,
                row_num=lines[0],
                business_key=bk_viol,
                message=f"дубликат ключа {key_columns}={kt}, строки {lines}",
                severity="warning",
            )
        )
    return out


def run_unique_merged(
    rule: dict[str, Any],
    entity: str,
    merged_rows: list[MergedRow],
    key_columns: list[str] | None,
    use_business_key: bool,
) -> list[ConsistencyViolation]:
    rid = str(rule.get("id", "unique_merged"))
    counts: dict[tuple[str, ...], list[int]] = {}
    for idx, mr in enumerate(merged_rows):
        if use_business_key or not key_columns:
            kt = (mr.business_key,)
        else:
            kt = _key_tuple_from_row({k: str(v) for k, v in mr.merged_data.items()}, key_columns)
        counts.setdefault(kt, []).append(idx)
    out: list[ConsistencyViolation] = []
    for kt, idxs in counts.items():
        if len(idxs) <= 1:
            continue
        out.append(
            ConsistencyViolation(
                rule_id=rid,
                rule_type="unique",
                scope="merged",
                entity=entity,
                stand=None,
                row_num=None,
                business_key=kt[0] if len(kt) == 1 else "|".join(kt),
                message=f"дубликат merged-ключа, индексы строк вывода {idxs}",
                severity="warning",
            )
        )
    return out


def run_field_length(
    rule: dict[str, Any],
    entity: str,
    stand: str | None,
    rows_data: list[tuple[int | None, dict[str, str], str | None]],
    fields_spec: dict[str, Any],
) -> list[ConsistencyViolation]:
    """fields_spec: колонка -> {limit, operator: <=|=|>=}"""
    rid = str(rule.get("id", "field_length"))
    out: list[ConsistencyViolation] = []
    for col, spec in fields_spec.items():
        if not isinstance(spec, dict):
            continue
        limit = int(spec.get("limit", 0))
        op = str(spec.get("operator", "<=")).strip()
        for row_num, data, bk in rows_data:
            val = str(data.get(col, ""))
            ln = len(val)
            ok = True
            if op in ("<=", "=<"):
                ok = ln <= limit
            elif op == ">=":
                ok = ln >= limit
            elif op in ("=", "=="):
                ok = ln == limit
            else:
                ok = ln <= limit
            if not ok:
                out.append(
                    ConsistencyViolation(
                        rule_id=rid,
                        rule_type="field_length",
                        scope=str(rule.get("_runtime_scope", "per_stand")),
                        entity=entity,
                        stand=stand,
                        row_num=row_num,
                        business_key=bk,
                        message=f"{col}: len={ln}, ожидание {op}{limit}",
                        severity="warning",
                    )
                )
    return out


def _check_date(value: str, fmt: str) -> bool:
    value = value.strip()
    if not value:
        return True
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def _check_decimal(value: str) -> bool:
    value = value.strip()
    if not value:
        return True
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", value))


def _check_fixed_digits(value: str, length: int) -> bool:
    value = value.strip()
    if not value:
        return True
    return bool(re.fullmatch(rf"\d{{{length}}}", value))


def run_field_format(
    rule: dict[str, Any],
    entity: str,
    stand: str | None,
    rows_data: list[tuple[int | None, dict[str, str], str | None]],
) -> list[ConsistencyViolation]:
    rid = str(rule.get("id", "field_format"))
    column = str(rule.get("column", "")).strip()
    fmt = str(rule.get("format_type", "")).strip().lower()
    params = rule.get("params") or {}
    out: list[ConsistencyViolation] = []
    for row_num, data, bk in rows_data:
        val = str(data.get(column, ""))
        ok = True
        allow_empty = bool(params.get("allow_empty", True))
        special_values = {str(x) for x in (params.get("special_values") or [])}
        if val.strip() in special_values:
            ok = True
        elif not val.strip() and allow_empty:
            ok = True
        elif not val.strip() and not allow_empty:
            ok = False
        elif fmt == "date":
            ok = _check_date(val, str(params.get("strftime", "%Y-%m-%d")))
        elif fmt == "decimal":
            ok = _check_decimal(val)
            dp = params.get("decimal_places")
            if ok and dp is not None and val.strip():
                m = re.fullmatch(r"-?\d+(\.(\d+))?", val.strip())
                frac = (m.group(2) if m else "") or ""
                ok = len(frac) == int(dp)
        elif fmt == "fixed_length_digits":
            ok = _check_fixed_digits(val, int(params.get("length", 1)))
        else:
            ok = True
        if not ok:
            out.append(
                ConsistencyViolation(
                    rule_id=rid,
                    rule_type="field_format",
                    scope=str(rule.get("_runtime_scope", "per_stand")),
                    entity=entity,
                    stand=stand,
                    row_num=row_num,
                    business_key=bk,
                    message=f"{column}={val!r} не соответствует {fmt} {params}",
                    severity="warning",
                )
            )
    return out


def run_referential(
    rule: dict[str, Any],
    entity: str,
    stand: str | None,
    parsed_by_entity: dict[str, list[ParsedRow]],
    merged_rows: list[MergedRow] | None,
    scope: str,
) -> list[ConsistencyViolation]:
    """Ссылка source_column (или source_columns) -> target_entity + target_key_columns."""
    rid = str(rule.get("id", "referential"))
    src_cols = rule.get("source_columns")
    if isinstance(src_cols, list) and src_cols:
        source_columns = [str(c).strip() for c in src_cols]
    else:
        source_columns = [str(rule.get("source_column", "")).strip()]
    target_entity = str(rule.get("target_entity", "")).strip()
    target_key_columns = [str(c).strip() for c in (rule.get("target_key_columns") or [])]
    out: list[ConsistencyViolation] = []
    if not target_entity or not target_key_columns:
        return out
    src_conds = _normalize_rule_conditions(rule, "src_row")
    ref_conds = _normalize_rule_conditions(rule, "ref_row")
    src_mode = str(rule.get("src_row_conditions_mode", "all")).strip().lower()
    ref_mode = str(rule.get("ref_row_conditions_mode", "all")).strip().lower()

    if scope == "per_stand" and stand is not None:
        targets = [
            pr
            for pr in parsed_by_entity.get(target_entity, [])
            if pr.stand == stand and _row_matches_conditions(pr.data, ref_conds, ref_mode)
        ]
        tset = _build_target_key_set(targets, target_key_columns)
        for pr in parsed_by_entity.get(entity, []):
            if pr.stand != stand:
                continue
            if not _row_matches_conditions(pr.data, src_conds, src_mode):
                continue
            kt = _key_tuple_from_row(pr.data, source_columns)
            if kt == tuple("" for _ in source_columns):
                continue
            if kt not in tset:
                out.append(
                    ConsistencyViolation(
                        rule_id=rid,
                        rule_type="referential",
                        scope="per_stand",
                        entity=entity,
                        stand=stand,
                        row_num=pr.row_num,
                        business_key=pr.business_key,
                        message=(
                            f"значение {source_columns}={kt} не найдено в "
                            f"{target_entity}.{target_key_columns} на стенде {stand}"
                        ),
                        severity="error",
                    )
                )
        return out

    # merged: значения из merged_data должны попадать в объединённый набор ключей target по всем стендам
    if scope == "merged" and merged_rows is not None:
        targets = [
            pr
            for pr in parsed_by_entity.get(target_entity, [])
            if _row_matches_conditions(pr.data, ref_conds, ref_mode)
        ]
        tset = _build_target_key_set(targets, target_key_columns)
        for mr in merged_rows:
            if mr.entity != entity:
                continue
            row = {k: str(v) for k, v in mr.merged_data.items()}
            if not _row_matches_conditions(row, src_conds, src_mode):
                continue
            kt = _key_tuple_from_row(row, source_columns)
            if kt == tuple("" for _ in source_columns):
                continue
            if kt not in tset:
                out.append(
                    ConsistencyViolation(
                        rule_id=rid,
                        rule_type="referential",
                        scope="merged",
                        entity=entity,
                        stand=None,
                        row_num=None,
                        business_key=mr.business_key,
                        message=(
                            f"merged: {source_columns}={kt} не найдено среди всех строк "
                            f"{target_entity}.{target_key_columns}"
                        ),
                        severity="error",
                    )
                )
    return out


def run_referential_composite(
    rule: dict[str, Any],
    entity: str,
    stand: str | None,
    parsed_by_entity: dict[str, list[ParsedRow]],
    merged_rows: list[MergedRow] | None,
    scope: str,
) -> list[ConsistencyViolation]:
    """Как referential, но source_columns — несколько колонок одним составным ключом."""
    return run_referential(rule, entity, stand, parsed_by_entity, merged_rows, scope)


def run_cross_sheet_date_lte_today(
    rule: dict[str, Any],
    entity: str,
    stand: str | None,
    rows_data: list[tuple[int | None, dict[str, str], str | None]],
    parsed_by_entity: dict[str, list[ParsedRow]] | None = None,
) -> list[ConsistencyViolation]:
    rid = str(rule.get("id", "cross_sheet_date_lte_today"))
    column = str(rule.get("column", "")).strip()
    fmt = str(rule.get("date_format", "%Y-%m-%d"))
    ref_entity = str(rule.get("sheet_ref", rule.get("ref_entity", ""))).strip()
    src_key = str(rule.get("column_src", "")).strip()
    ref_key = str(rule.get("column_ref", "")).strip()
    ref_date = str(rule.get("column_date_ref", "")).strip()
    today = datetime.now().date()
    out: list[ConsistencyViolation] = []
    ref_map: dict[str, str] = {}
    if parsed_by_entity is not None and ref_entity and src_key and ref_key and ref_date:
        for pr in parsed_by_entity.get(ref_entity, []):
            if stand is not None and pr.stand != stand:
                continue
            rk = _normalize_cell_for_compare(pr.data.get(ref_key, ""))
            if not rk:
                continue
            if rk not in ref_map:
                ref_map[rk] = str(pr.data.get(ref_date, "")).strip()
    for row_num, data, bk in rows_data:
        if ref_map:
            src_val = _normalize_cell_for_compare(data.get(src_key, ""))
            val = ref_map.get(src_val, "")
        else:
            val = str(data.get(column, "")).strip()
        if not val:
            continue
        try:
            d = datetime.strptime(val, fmt).date()
        except ValueError:
            out.append(
                ConsistencyViolation(
                    rule_id=rid,
                    rule_type="cross_sheet_date_lte_today",
                    scope=str(rule.get("_runtime_scope", "per_stand")),
                    entity=entity,
                    stand=stand,
                    row_num=row_num,
                    business_key=bk,
                    message=f"{column}={val!r} не разобрать как дату ({fmt})",
                    severity="warning",
                )
            )
            continue
        if d > today:
            out.append(
                ConsistencyViolation(
                    rule_id=rid,
                    rule_type="cross_sheet_date_lte_today",
                    scope=str(rule.get("_runtime_scope", "per_stand")),
                    entity=entity,
                    stand=stand,
                    row_num=row_num,
                    business_key=bk,
                    message=f"{column}={val} ({d}) позже сегодня ({today})",
                    severity="warning",
                )
            )
    return out


def _normalize_spod_json_cell(raw: str) -> str:
    s = raw.strip()
    s = s.replace('"""', '"')
    return s


def _get_json_path(obj: Any, path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        else:
            return None
    return cur


def run_json_spod_format(
    rule: dict[str, Any],
    entity: str,
    stand: str | None,
    rows_data: list[tuple[int | None, dict[str, str], str | None]],
) -> list[ConsistencyViolation]:
    rid = str(rule.get("id", "json_spod_format"))
    column = str(rule.get("json_column", rule.get("column", ""))).strip()
    required = bool(rule.get("json_required", False))
    numeric_keys = {str(k).strip() for k in (rule.get("numeric_value_keys") or []) if str(k).strip()}
    out: list[ConsistencyViolation] = []
    for row_num, data, bk in rows_data:
        raw = str(data.get(column, "")).strip()
        if not raw:
            if required:
                out.append(
                    ConsistencyViolation(
                        rule_id=rid,
                        rule_type="json_spod_format",
                        scope=str(rule.get("_runtime_scope", "per_stand")),
                        entity=entity,
                        stand=stand,
                        row_num=row_num,
                        business_key=bk,
                        message=f"{column}: пусто при json_required=true",
                        severity="error",
                    )
                )
            continue
        try:
            obj = json.loads(_normalize_spod_json_cell(raw))
        except json.JSONDecodeError as exc:
            out.append(
                ConsistencyViolation(
                    rule_id=rid,
                    rule_type="json_spod_format",
                    scope=str(rule.get("_runtime_scope", "per_stand")),
                    entity=entity,
                    stand=stand,
                    row_num=row_num,
                    business_key=bk,
                    message=f"{column}: JSON ошибка: {exc}",
                    severity="error",
                )
            )
            continue
        if numeric_keys and isinstance(obj, dict):
            bad: list[str] = []
            for k in numeric_keys:
                if k not in obj:
                    continue
                v = obj.get(k)
                if isinstance(v, (int, float)):
                    continue
                if isinstance(v, str) and _check_decimal(v):
                    continue
                bad.append(k)
            if bad:
                out.append(
                    ConsistencyViolation(
                        rule_id=rid,
                        rule_type="json_spod_format",
                        scope=str(rule.get("_runtime_scope", "per_stand")),
                        entity=entity,
                        stand=stand,
                        row_num=row_num,
                        business_key=bk,
                        message=f"{column}: numeric_value_keys нечисловые: {bad}",
                        severity="error",
                    )
                )
    return out


def run_json_field_equals_column(
    rule: dict[str, Any],
    entity: str,
    stand: str | None,
    rows_data: list[tuple[int | None, dict[str, str], str | None]],
) -> list[ConsistencyViolation]:
    rid = str(rule.get("id", "json_field_equals_column"))
    jcol = str(rule.get("json_column", "")).strip()
    path = str(rule.get("json_path", "")).strip()
    cmp_col = str(rule.get("compare_column", "")).strip()
    must_not_equal = bool(rule.get("must_not_equal", False))
    filter_col = str(rule.get("filter_column", "")).strip()
    filter_val = str(rule.get("filter_value", "")).strip()
    j_filter_key = str(rule.get("json_filter_key", "")).strip()
    j_filter_val = str(rule.get("json_filter_value", "")).strip()
    out: list[ConsistencyViolation] = []
    for row_num, data, bk in rows_data:
        if filter_col:
            if _normalize_cell_for_compare(data.get(filter_col, "")) != _normalize_cell_for_compare(filter_val):
                continue
        raw = str(data.get(jcol, "")).strip()
        if not raw:
            continue
        try:
            obj = json.loads(_normalize_spod_json_cell(raw))
        except json.JSONDecodeError:
            continue
        if j_filter_key:
            jf = _get_json_path(obj, j_filter_key)
            if _normalize_cell_for_compare(jf) != _normalize_cell_for_compare(j_filter_val):
                continue
        jv = _get_json_path(obj, path)
        cv = str(data.get(cmp_col, "")).strip()
        is_equal = _normalize_cell_for_compare(jv) == _normalize_cell_for_compare(cv)
        bad = is_equal if must_not_equal else not is_equal
        if bad:
            out.append(
                ConsistencyViolation(
                    rule_id=rid,
                    rule_type="json_field_equals_column",
                    scope=str(rule.get("_runtime_scope", "per_stand")),
                    entity=entity,
                    stand=stand,
                    row_num=row_num,
                    business_key=bk,
                    message=(
                        f"{jcol}.{path}={jv!r} == {cmp_col}={cv!r}"
                        if must_not_equal
                        else f"{jcol}.{path}={jv!r} != {cmp_col}={cv!r}"
                    ),
                    severity="warning",
                )
            )
    return out


def run_json_field_in_column(
    rule: dict[str, Any],
    entity: str,
    stand: str | None,
    rows_data: list[tuple[int | None, dict[str, str], str | None]],
    all_rows_data: list[tuple[int | None, dict[str, str], str | None]] | None = None,
) -> list[ConsistencyViolation]:
    rid = str(rule.get("id", "json_field_in_column"))
    jcol = str(rule.get("json_column", "")).strip()
    path = str(rule.get("json_path", "")).strip()
    allowed_col = str(rule.get("values_column", "")).strip()
    column_in_sheet = str(rule.get("column_in_sheet", "")).strip()
    sep = str(rule.get("values_separator", ";"))
    out: list[ConsistencyViolation] = []
    allowed_set_sheet: set[str] = set()
    src_rows_for_set = all_rows_data if all_rows_data is not None else rows_data
    if column_in_sheet:
        for _, d, _ in src_rows_for_set:
            v = _normalize_cell_for_compare(d.get(column_in_sheet, ""))
            if v:
                allowed_set_sheet.add(v)
    for row_num, data, bk in rows_data:
        raw = str(data.get(jcol, "")).strip()
        if not raw:
            continue
        try:
            obj = json.loads(_normalize_spod_json_cell(raw))
        except json.JSONDecodeError:
            continue
        jv = str(_get_json_path(obj, path)).strip()
        allowed_raw = str(data.get(allowed_col, "")).strip()
        allowed = {x.strip() for x in allowed_raw.split(sep) if x.strip()}
        if allowed_set_sheet:
            allowed |= allowed_set_sheet
        if jv and jv not in allowed:
            out.append(
                ConsistencyViolation(
                    rule_id=rid,
                    rule_type="json_field_in_column",
                    scope=str(rule.get("_runtime_scope", "per_stand")),
                    entity=entity,
                    stand=stand,
                    row_num=row_num,
                    business_key=bk,
                    message=f"{jcol}.{path}={jv!r} не в {allowed_col}={allowed_raw!r}",
                    severity="warning",
                )
            )
    return out


def run_json_priority_unique_per_contest_link(
    rule: dict[str, Any],
    entity: str,
    stand: str | None,
    rows_data: list[tuple[int | None, dict[str, str], str | None]],
    parsed_by_entity: dict[str, list[ParsedRow]] | None = None,
) -> list[ConsistencyViolation]:
    """
    Упрощённая проверка: в одной строке JSON-массив в json_column;
    уникальность значения json_path внутри массива по элементам.
    """
    rid = str(rule.get("id", "json_priority_unique"))
    jcol = str(rule.get("json_column", "")).strip()
    path = str(rule.get("json_path", "priority")).strip()
    reward_code_col = str(rule.get("reward_code_column", "REWARD_CODE")).strip()
    link_entity = str(rule.get("link_sheet", "")).strip()
    link_contest_col = str(rule.get("link_contest_column", "CONTEST_CODE")).strip()
    link_reward_col = str(rule.get("link_reward_column", "REWARD_CODE")).strip()
    out: list[ConsistencyViolation] = []
    if parsed_by_entity is not None and link_entity:
        rewards_by_code: dict[str, tuple[int | None, dict[str, str], str | None]] = {}
        for rn, d, bk in rows_data:
            rc = _normalize_cell_for_compare(d.get(reward_code_col, ""))
            if rc and rc not in rewards_by_code:
                rewards_by_code[rc] = (rn, d, bk)
        by_contest: dict[str, set[str]] = {}
        for pr in parsed_by_entity.get(link_entity, []):
            if stand is not None and pr.stand != stand:
                continue
            contest = _normalize_cell_for_compare(pr.data.get(link_contest_col, ""))
            reward_code = _normalize_cell_for_compare(pr.data.get(link_reward_col, ""))
            if not contest or not reward_code:
                continue
            by_contest.setdefault(contest, set()).add(reward_code)
        for contest, reward_codes in by_contest.items():
            seen: dict[str, str] = {}
            missing: list[str] = []
            for rc in reward_codes:
                rr = rewards_by_code.get(rc)
                if rr is None:
                    continue
                row_num, data, bk = rr
                raw = str(data.get(jcol, "")).strip()
                if not raw:
                    missing.append(rc)
                    continue
                try:
                    obj = json.loads(_normalize_spod_json_cell(raw))
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    pv = _normalize_cell_for_compare(_get_json_path(obj, path))
                else:
                    pv = ""
                if not pv:
                    missing.append(rc)
                    continue
                if pv in seen:
                    out.append(
                        ConsistencyViolation(
                            rule_id=rid,
                            rule_type="json_priority_unique_per_contest_link",
                            scope=str(rule.get("_runtime_scope", "per_stand")),
                            entity=entity,
                            stand=stand,
                            row_num=row_num,
                            business_key=bk,
                            message=f"contest={contest}: priority={pv} повтор для reward {rc} и {seen[pv]}",
                            severity="warning",
                        )
                    )
                else:
                    seen[pv] = rc
            if missing and len(missing) != len(reward_codes):
                out.append(
                    ConsistencyViolation(
                        rule_id=rid,
                        rule_type="json_priority_unique_per_contest_link",
                        scope=str(rule.get("_runtime_scope", "per_stand")),
                        entity=entity,
                        stand=stand,
                        row_num=None,
                        business_key=None,
                        message=f"contest={contest}: часть reward без priority ({missing})",
                        severity="warning",
                    )
                )
        return out
    for row_num, data, bk in rows_data:
        raw = str(data.get(jcol, "")).strip()
        if not raw:
            continue
        try:
            obj = json.loads(_normalize_spod_json_cell(raw))
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, list):
            continue
        seen: set[str] = set()
        dups: list[str] = []
        for el in obj:
            if not isinstance(el, dict):
                continue
            v = str(el.get(path.split(".")[-1], el.get(path, ""))).strip()
            if v in seen:
                dups.append(v)
            seen.add(v)
        if dups:
            out.append(
                ConsistencyViolation(
                    rule_id=rid,
                    rule_type="json_priority_unique_per_contest_link",
                    scope=str(rule.get("_runtime_scope", "per_stand")),
                    entity=entity,
                    stand=stand,
                    row_num=row_num,
                    business_key=bk,
                    message=f"{jcol}: повтор {path} внутри массива: {dups}",
                    severity="warning",
                )
            )
    return out


RUNNERS: dict[str, Callable[..., list[ConsistencyViolation]]] = {
    "csv_columns_count": run_csv_columns_count_for_stand,
    "unique": run_unique_per_stand,
    "field_length": run_field_length,
    "field_format": run_field_format,
    "referential": run_referential,
    "referential_composite": run_referential_composite,
    "cross_sheet_date_lte_today": run_cross_sheet_date_lte_today,
    "json_spod_format": run_json_spod_format,
    "json_field_equals_column": run_json_field_equals_column,
    "json_field_in_column": run_json_field_in_column,
    "json_priority_unique_per_contest_link": run_json_priority_unique_per_contest_link,
}


def max_header_len_across_stands(
    entity: str, field_orders: dict[str, dict[str, list[str]]], stands: list[str]
) -> int:
    m = 0
    for st in stands:
        m = max(m, len(field_orders.get(entity, {}).get(st, [])))
    return m


@dataclass
class ConsistencyRunResult:
    """Итог одного прогона проверок."""

    violations: list[ConsistencyViolation] = field(default_factory=list)
    """Имя колонки на листе сущности -> значение для каждой merged-строки (порядок совпадает с merged_rows)."""

    merged_column_values: dict[str, dict[str, list[str]]] = field(default_factory=dict)


def execute_consistency_checks(
    *,
    config: dict[str, Any],
    stands: list[str],
    entities: dict[str, Any],
    parsed_by_entity: dict[str, list[ParsedRow]],
    merged_by_entity: dict[str, list[MergedRow]],
    field_orders: dict[str, dict[str, list[str]]],
    logger: Any,
) -> ConsistencyRunResult:
    """Запускает все включённые правила с учётом scope."""
    result = ConsistencyRunResult()
    cc = config.get("consistency_checks") or {}
    if not is_consistency_checks_enabled(cc):
        return result
    raw_rules = list(cc.get("rules") or [])
    rules: list[dict[str, Any]] = [_normalize_rule_schema(r) for r in raw_rules]
    csv_block = cc.get("csv_columns_count") or {}
    csv_entities = csv_block.get("entities") or {}
    csv_sheets = csv_block.get("sheets") or {}
    csv_cc: dict[str, Any] = {}
    for en, spec in csv_entities.items():
        csv_cc[_sheet_alias_to_entity_name(en)] = spec
    for sh, spec in csv_sheets.items():
        csv_cc[_sheet_alias_to_entity_name(sh)] = spec

    # --- csv_columns_count (per entity, each stand) ---
    default_rule = {"id": "csv_columns_auto", "type": "csv_columns_count"}
    for entity in entities:
        max_h = max_header_len_across_stands(entity, field_orders, stands)
        expected_cfg = csv_cc.get(entity, {})
        exp = expected_cfg.get("expected_columns")
        if exp is None:
            exp = 0
        for stand in stands:
            rows = [pr for pr in parsed_by_entity.get(entity, []) if pr.stand == stand]
            headers = list(field_orders.get(entity, {}).get(stand, []))
            if not rows and not headers:
                continue
            result.violations.extend(
                run_csv_columns_count_for_stand(
                    default_rule,
                    entity,
                    stand,
                    headers,
                    rows,
                    int(exp) if exp is not None else 0,
                    max_h,
                )
            )

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        rtype = str(rule.get("type", "")).strip()
        entity = str(rule.get("entity", "")).strip()
        if not entity or entity not in entities:
            continue
        # По требованию проекта все проверки unique всегда выполняются
        # и по стендам (per_stand), и по итоговым merged-листам.
        scopes = ["per_stand", "merged"] if rtype == "unique" else _scopes_for_rule(rule)
        for sc in scopes:
            if rtype == "unique":
                key_cols = rule.get("key_columns")
                if not isinstance(key_cols, list) or not key_cols:
                    key_cols = list(entities[entity].get("business_key") or [])
                use_bk = bool(rule.get("use_business_key", False))
                if sc == "per_stand":
                    for stand in stands:
                        rows = [pr for pr in parsed_by_entity.get(entity, []) if pr.stand == stand]
                        rule_copy = dict(rule)
                        rule_copy["_runtime_scope"] = "per_stand"
                        result.violations.extend(
                            run_unique_per_stand(rule_copy, entity, stand, rows, key_cols)
                        )
                else:
                    rule_copy = dict(rule)
                    rule_copy["_runtime_scope"] = "merged"
                    result.violations.extend(
                        run_unique_merged(
                            rule_copy,
                            entity,
                            merged_by_entity.get(entity, []),
                            key_cols if not use_bk else None,
                            use_bk,
                        )
                    )
                continue

            if rtype == "field_length":
                fields_spec = rule.get("fields") or {}
                if sc == "per_stand":
                    for stand in stands:
                        rows = [
                            (pr.row_num, pr.data, pr.business_key)
                            for pr in parsed_by_entity.get(entity, [])
                            if pr.stand == stand
                        ]
                        rc = dict(rule)
                        rc["_runtime_scope"] = "per_stand"
                        result.violations.extend(
                            run_field_length(rc, entity, stand, rows, fields_spec)
                        )
                else:
                    rows = [
                        (None, {k: str(v) for k, v in mr.merged_data.items()}, mr.business_key)
                        for mr in merged_by_entity.get(entity, [])
                    ]
                    rc = dict(rule)
                    rc["_runtime_scope"] = "merged"
                    result.violations.extend(run_field_length(rc, entity, None, rows, fields_spec))
                continue

            if rtype == "field_format":
                if sc == "per_stand":
                    for stand in stands:
                        rows = [
                            (pr.row_num, pr.data, pr.business_key)
                            for pr in parsed_by_entity.get(entity, [])
                            if pr.stand == stand
                        ]
                        rc = dict(rule)
                        rc["_runtime_scope"] = "per_stand"
                        result.violations.extend(run_field_format(rc, entity, stand, rows))
                else:
                    rows = [
                        (None, {k: str(v) for k, v in mr.merged_data.items()}, mr.business_key)
                        for mr in merged_by_entity.get(entity, [])
                    ]
                    rc = dict(rule)
                    rc["_runtime_scope"] = "merged"
                    result.violations.extend(run_field_format(rc, entity, None, rows))
                continue

            if rtype == "referential" or rtype == "referential_composite":
                if sc == "per_stand":
                    for stand in stands:
                        result.violations.extend(
                            run_referential(
                                rule, entity, stand, parsed_by_entity, None, "per_stand"
                            )
                        )
                else:
                    result.violations.extend(
                        run_referential(
                            rule,
                            entity,
                            None,
                            parsed_by_entity,
                            merged_by_entity.get(entity, []),
                            "merged",
                        )
                    )
                continue

            if rtype == "cross_sheet_date_lte_today":
                if sc == "per_stand":
                    for stand in stands:
                        rows = [
                            (pr.row_num, pr.data, pr.business_key)
                            for pr in parsed_by_entity.get(entity, [])
                            if pr.stand == stand
                        ]
                        rc = dict(rule)
                        rc["_runtime_scope"] = "per_stand"
                        result.violations.extend(
                            run_cross_sheet_date_lte_today(
                                rc,
                                entity,
                                stand,
                                rows,
                                parsed_by_entity,
                            )
                        )
                else:
                    rows = [
                        (None, {k: str(v) for k, v in mr.merged_data.items()}, mr.business_key)
                        for mr in merged_by_entity.get(entity, [])
                    ]
                    rc = dict(rule)
                    rc["_runtime_scope"] = "merged"
                    result.violations.extend(
                        run_cross_sheet_date_lte_today(
                            rc,
                            entity,
                            None,
                            rows,
                            parsed_by_entity,
                        )
                    )
                continue

            if rtype == "json_field_in_column":
                if sc == "per_stand":
                    for stand in stands:
                        rows = [
                            (pr.row_num, pr.data, pr.business_key)
                            for pr in parsed_by_entity.get(entity, [])
                            if pr.stand == stand
                        ]
                        rc = dict(rule)
                        rc["_runtime_scope"] = "per_stand"
                        result.violations.extend(
                            run_json_field_in_column(
                                rc,
                                entity,
                                stand,
                                rows,
                                rows,
                            )
                        )
                else:
                    rows = [
                        (None, {k: str(v) for k, v in mr.merged_data.items()}, mr.business_key)
                        for mr in merged_by_entity.get(entity, [])
                    ]
                    rc = dict(rule)
                    rc["_runtime_scope"] = "merged"
                    result.violations.extend(
                        run_json_field_in_column(
                            rc,
                            entity,
                            None,
                            rows,
                            rows,
                        )
                    )
                continue

            if rtype == "json_priority_unique_per_contest_link":
                if sc == "per_stand":
                    for stand in stands:
                        rows = [
                            (pr.row_num, pr.data, pr.business_key)
                            for pr in parsed_by_entity.get(entity, [])
                            if pr.stand == stand
                        ]
                        rc = dict(rule)
                        rc["_runtime_scope"] = "per_stand"
                        result.violations.extend(
                            run_json_priority_unique_per_contest_link(
                                rc,
                                entity,
                                stand,
                                rows,
                                parsed_by_entity,
                            )
                        )
                else:
                    rows = [
                        (None, {k: str(v) for k, v in mr.merged_data.items()}, mr.business_key)
                        for mr in merged_by_entity.get(entity, [])
                    ]
                    rc = dict(rule)
                    rc["_runtime_scope"] = "merged"
                    result.violations.extend(
                        run_json_priority_unique_per_contest_link(
                            rc,
                            entity,
                            None,
                            rows,
                            parsed_by_entity,
                        )
                    )
                continue

            if rtype in RUNNERS and rtype.startswith("json"):
                if sc == "per_stand":
                    for stand in stands:
                        rows = [
                            (pr.row_num, pr.data, pr.business_key)
                            for pr in parsed_by_entity.get(entity, [])
                            if pr.stand == stand
                        ]
                        rc = dict(rule)
                        rc["_runtime_scope"] = "per_stand"
                        result.violations.extend(RUNNERS[rtype](rc, entity, stand, rows))
                else:
                    rows = [
                        (None, {k: str(v) for k, v in mr.merged_data.items()}, mr.business_key)
                        for mr in merged_by_entity.get(entity, [])
                    ]
                    rc = dict(rule)
                    rc["_runtime_scope"] = "merged"
                    result.violations.extend(RUNNERS[rtype](rc, entity, None, rows))
                continue

    # Заполнение колонок на merged-листах: агрегат по business_key для per_stand + текст для merged
    _inject_consistency_columns_into_merged(
        result,
        cc,
        rules,
        merged_by_entity,
        parsed_by_entity,
        entities,
    )

    if result.violations:
        logger.warning(
            "Проверки консистентности: всего отклонений=%s",
            len(result.violations),
            extra={"class_name": "Consistency", "func_name": "execute"},
        )
    fail_fast = bool(cc.get("fail_fast", False))
    if fail_fast:
        errors = [v for v in result.violations if v.severity == "error"]
        if errors:
            msg = "; ".join(f"{e.rule_id}: {e.message}" for e in errors[:5])
            raise ValueError(f"consistency_checks fail_fast: {msg}")
    return result


def _inject_consistency_columns_into_merged(
    result: ConsistencyRunResult,
    cc: dict[str, Any],
    rules: list[dict[str, Any]],
    merged_by_entity: dict[str, list[MergedRow]],
    parsed_by_entity: dict[str, list[ParsedRow]],
    entities: dict[str, Any],
) -> None:
    """Добавляет в merged_data ключи колонок с кратким статусом OK / текст ошибок по правилу."""
    for entity, mrows in merged_by_entity.items():
        for mr in mrows:
            bk = mr.business_key
            parts: list[str] = []
            for v in result.violations:
                if v.entity != entity:
                    continue
                if v.business_key is None or v.business_key != bk:
                    continue
                parts.append(f"{v.rule_id}:{v.message}")
            if parts:
                mr.merged_data["CONSIST_ROW_DETAIL"] = " | ".join(parts[:12])
            else:
                mr.merged_data.setdefault("CONSIST_ROW_DETAIL", "")

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        rtype = str(rule.get("type", ""))
        entity = str(rule.get("entity", ""))
        if entity not in merged_by_entity:
            continue
        scopes = _scopes_for_rule(rule)
        suf_m = str(rule.get("output", {}).get("column_suffix_merged", "")).strip()
        suf_s = str(rule.get("output", {}).get("column_suffix_per_stand", "")).strip()
        suf_one = str(rule.get("output", {}).get("column_suffix", "")).strip()
        rid = str(rule.get("id", rtype))
        if "merged" in scopes:
            col_m = suf_m or (f"CC_{rid}_M" if not suf_one else f"CC_{suf_one}_M")
            for mr in merged_by_entity[entity]:
                msgs = [
                    v.message
                    for v in result.violations
                    if v.entity == entity
                    and v.business_key == mr.business_key
                    and v.scope == "merged"
                    and v.rule_id == rid
                ]
                mr.merged_data[col_m] = "OK" if not msgs else "; ".join(msgs)[:500]
        if "per_stand" in scopes:
            col_s = suf_s or (f"CC_{rid}_S" if not suf_one else f"CC_{suf_one}_S")
            for mr in merged_by_entity[entity]:
                msgs = [
                    f"{v.stand}:{v.message}"
                    for v in result.violations
                    if v.entity == entity
                    and v.business_key == mr.business_key
                    and v.scope == "per_stand"
                    and v.rule_id == rid
                ]
                mr.merged_data[col_s] = "OK" if not msgs else "; ".join(msgs)[:500]

    for entity, mrows in merged_by_entity.items():
        for mr in mrows:
            any_stand = [
                f"{v.stand}:{v.rule_id}:{v.message}"
                for v in result.violations
                if v.entity == entity
                and v.business_key == mr.business_key
                and v.scope == "per_stand"
            ]
            if any_stand:
                mr.merged_data.setdefault(
                    "CONSIST_ALL_STAND_ISSUES",
                    " | ".join(any_stand[:8]),
                )

    # Автоправило числа колонок (не в массиве rules): колонка на листе сущности
    csv_rid = "csv_columns_auto"
    csv_block = cc.get("csv_columns_count") or {}
    csv_out = csv_block.get("output") or {}
    suf_s = str(csv_out.get("column_suffix_per_stand", "")).strip() or "CC_CSV_COLS_S"
    for entity, mrows in merged_by_entity.items():
        for mr in mrows:
            msgs = [
                f"{v.stand}:{v.message}"
                for v in result.violations
                if v.rule_id == csv_rid
                and v.entity == entity
                and v.scope == "per_stand"
                and v.business_key == mr.business_key
            ]
            mr.merged_data[suf_s] = "OK" if not msgs else "; ".join(msgs)[:500]


def append_consistency_sheet(
    workbook: Any,
    cc: dict[str, Any],
    violations: list[ConsistencyViolation],
    stands: list[str] | None = None,
) -> Any:
    """Добавляет лист CONSISTENCY: свод по правилам в разрезе стендов."""
    stands = list(stands or [])

    def _rule_type_label(rt: str) -> str:
        mapping = {
            "csv_columns_count": "число полей в CSV",
            "unique": "уникальность",
            "field_length": "длина поля",
            "field_format": "формат поля",
            "referential": "ссылочная целостность",
            "referential_composite": "ссылочная целостность (составной ключ)",
            "cross_sheet_date_lte_today": "дата <= сегодня",
            "json_spod_format": "формат SPOD-JSON",
            "json_field_equals_column": "JSON поле = колонке",
            "json_field_in_column": "JSON поле в колонке",
            "json_priority_unique_per_contest_link": "JSON priority уникален по contest",
        }
        return mapping.get(rt, rt)

    def _rule_source_table(rule: dict[str, Any]) -> str:
        return str(rule.get("sheet_src", rule.get("sheet", rule.get("entity", "")))).strip()

    def _rule_source_field(rule: dict[str, Any]) -> str:
        for k in ("column_src", "source_column", "field", "column", "json_column"):
            if rule.get(k):
                return str(rule.get(k))
        cols = rule.get("columns_src") or rule.get("source_columns") or rule.get("key_columns")
        if isinstance(cols, list):
            return ",".join(str(x) for x in cols)
        return ""

    def _rule_target_table(rule: dict[str, Any]) -> str:
        return str(rule.get("sheet_ref", rule.get("target_entity", ""))).strip()

    def _rule_target_field(rule: dict[str, Any]) -> str:
        for k in ("column_ref", "compare_column", "column_compare", "column_in_sheet"):
            if rule.get(k):
                return str(rule.get(k))
        cols = rule.get("columns_ref") or rule.get("target_key_columns")
        if isinstance(cols, list):
            return ",".join(str(x) for x in cols)
        return ""

    def _rule_param(rule: dict[str, Any]) -> str:
        rt = str(rule.get("type", ""))
        if rt == "field_length":
            fs = rule.get("fields") or {}
            parts: list[str] = []
            if isinstance(fs, dict):
                for col, spec in fs.items():
                    if isinstance(spec, dict):
                        parts.append(f"{col} {spec.get('operator', '<=')}{spec.get('limit', '')}")
            return "; ".join(parts)
        if rt == "field_format":
            if rule.get("format"):
                return str(rule.get("format"))
            ft = str(rule.get("format_type", ""))
            params = rule.get("params") or {}
            return f"{ft} {params}".strip()
        if rt in {"referential", "referential_composite"}:
            return "source -> reference"
        if rt == "cross_sheet_date_lte_today":
            return f"date_format={rule.get('date_format', '%Y-%m-%d')}"
        return ""

    def _sample_text(rows: list[ConsistencyViolation], limit: int = 5) -> str:
        if not rows:
            return ""
        parts: list[str] = []
        for v in rows[:limit]:
            parts.append(
                f"{v.stand or 'NO_STAND'}|row={v.row_num if v.row_num is not None else '-'}|"
                f"bk={v.business_key or '-'}|{v.message}"
            )
        if len(rows) > limit:
            parts.append(f"... +{len(rows) - limit}")
        return " || ".join(parts)[:12000]

    name = str(cc.get("summary_sheet_name", "CONSISTENCY"))[:31]
    sheet = workbook.create_sheet(name)
    headers = [
        "ТИП ПРОВЕРКИ",
        "Описание",
        "таблица источник",
        "поле источник",
        "таблица где проверяем",
        "поле для проверки",
        "параметр сравнения",
        "комментарий",
        "check_id",
        "rule_type",
        "scope",
        "stand",
        "total_rows",
        "violations",
        "sample",
    ]
    sheet.append(headers)

    rules_raw = list(cc.get("rules") or [])
    rules: list[dict[str, Any]] = [_normalize_rule_schema(r) for r in rules_raw]
    by_rule_stand: dict[tuple[str, str], list[ConsistencyViolation]] = {}
    for v in violations:
        st = v.stand or "NO_STAND"
        by_rule_stand.setdefault((str(v.rule_id), st), []).append(v)

    for rule in rules:
        rid = str(rule.get("id", ""))
        if not rid:
            continue
        enabled = bool(rule.get("enabled", True))
        rt = str(rule.get("type", ""))
        src_t = _rule_source_table(rule)
        src_f = _rule_source_field(rule)
        dst_t = _rule_target_table(rule)
        dst_f = _rule_target_field(rule)
        param = _rule_param(rule)
        comment = "Правило отключено (enabled=false)." if not enabled else ""
        scopes = _scopes_for_rule(rule)

        stands_for_rule = list(stands) if "per_stand" in scopes else []
        if "merged" in scopes:
            stands_for_rule.append("NO_STAND")
        if not stands_for_rule:
            stands_for_rule = list(stands) or ["NO_STAND"]

        for st in stands_for_rule:
            rows = by_rule_stand.get((rid, st), [])
            sample = _sample_text(rows)
            if not sample and not enabled:
                sample = "Проверка не выполнялась: правило отключено."
            sheet.append(
                [
                    _rule_type_label(rt),
                    str(rule.get("name", rid)),
                    src_t,
                    src_f,
                    dst_t,
                    dst_f,
                    param,
                    comment,
                    rid,
                    rt,
                    ",".join(scopes),
                    st,
                    len(rows),
                    len(rows),
                    sample,
                ]
            )

    # Отдельные строки для авто-проверки csv_columns_count.
    csv_rows = [v for v in violations if v.rule_id == "csv_columns_auto"]
    if csv_rows:
        for st in stands or ["NO_STAND"]:
            rows = [v for v in csv_rows if (v.stand or "NO_STAND") == st]
            sheet.append(
                [
                    _rule_type_label("csv_columns_count"),
                    "Проверка числа полей CSV",
                    "",
                    "все поля строки",
                    "",
                    "",
                    "expected_columns / auto",
                    "",
                    "csv_columns_auto",
                    "csv_columns_count",
                    "per_stand",
                    st,
                    len(rows),
                    len(rows),
                    _sample_text(rows),
                ]
            )

    if sheet.max_row == 1:
        sheet.append(
            [
                "summary",
                "Нарушений не найдено; проверки консистентности выполнены.",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "all",
                0,
                0,
                "",
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:O1"
    return sheet
