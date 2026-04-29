# Сравнение правил консистентности: текущий проект vs SPOD_PARCE_LOAD

Документ фиксирует сравнение **по каждому правилу из текущего `config.json`** (блок `consistency_checks.rules`) с тем, как аналогичная проверка реализована в `SPOD_PARCE_LOAD`.

## Краткая легенда

- **Лист (текущий проект)**: в текущей реализации это поле `entity`; по смыслу соответствует листу выгрузки.
- **Лист (SPOD_PARCE_LOAD)**: листы из конфигурации/описания `consistency_checks` в проекте-эталоне.
- **Что смотрим**: какие поля/ключи участвуют в сравнении.
- **Что анализируем**: бизнес-смысл проверки и тип отклонений.

---

## Таблица по всем правилам

| Rule ID (текущий) | Type | Лист (текущий) | Что смотрим (текущий) | Что анализируем | Где в текущем | Аналог в SPOD_PARCE_LOAD | Лист (SPOD) | Где в SPOD | Статус/разница и причина |
|---|---|---|---|---|---|---|---|---|---|
| `uniq_group_key` | `unique` | `GROUP` | Бизнес-ключ (`use_business_key: true`), `scope=both` | Дубли ключа по стендам и в merged | `run_unique_per_stand()`, `run_unique_merged()` | `unique` | `GROUP` и др. листы | `src/consistency_checks.py` | Частично: нет `unique_scope_conditions` и `unique_require_non_empty`; причина — упрощённая реализация |
| `len_reward_code` | `field_length` | `REWARD` | `REWARD_CODE`, `len <= 128` | Контроль длины поля | `run_field_length()` | `field_length` | По `sheet` из правила | `src/consistency_checks.py` | Почти полное соответствие |
| `fmt_contest_create_dt` | `field_format` | `CONTEST` | `CREATE_DT`, формат `%Y-%m-%d` | Валидность формата даты | `run_field_format()`, `_check_date()` | `field_format` | По `sheet` из правила | `src/consistency_checks.py` | Частично: базовые форматы без расширенной логики |
| `ref_link_to_reward` (`enabled=false`) | `referential` | `REWARD-LINK` | `REWARD_CODE -> REWARD.REWARD_CODE`, `scope=merged` | Наличие ссылки в справочнике | `run_referential()` | `referential` | `REWARD-LINK -> REWARD` | `src/consistency_checks.py` | Частично: нет `src_row_conditions`/`ref_row_conditions`; правило выключено |
| `ref_group_to_contest` (`enabled=false`) | `referential` | `GROUP` | `CONTEST_CODE -> CONTEST.CONTEST_CODE`, `scope=both` | Целостность внешнего ключа конкурса | `run_referential()` | `referential` (например `ref_group_contest_code_in_contest_data`) | `GROUP -> CONTEST-DATA` | `src/consistency_checks.py` | Частично: нет row-фильтров; правило выключено |
| `ref_composite_example` (`enabled=false`) | `referential_composite` | `REWARD-LINK` | (`CONTEST_CODE`,`GROUP_CODE`,`REWARD_CODE`) -> target key | Целостность составного ключа | `run_referential_composite()` | `referential_composite` | Обычно `REWARD-LINK -> GROUP` | `src/consistency_checks.py` | Частично: нет `src_row_conditions`/`ref_row_conditions`; правило выключено |
| `cross_dt_create_lte_today` | `cross_sheet_date_lte_today` | `CONTEST` | `CREATE_DT <= today` (в той же строке) | Дата не в будущем | `run_cross_sheet_date_lte_today()` | `cross_sheet_date_lte_today` | Источник + справочник (напр. `REPORT -> TOURNAMENT-SCHEDULE`) | `src/consistency_checks.py` | Частично: нет межлистового lookup (`sheet_ref`,`column_ref`,`column_date_ref`), только локальная проверка |
| `json_contest_feature` | `json_spod_format` | `CONTEST` | `CONTEST_FEATURE` как JSON | Корректность JSON после `""" -> "` | `run_json_spod_format()` | `json_spod_format` | `CONTEST-DATA`, `REWARD`, `TOURNAMENT-SCHEDULE`, `INDICATOR` | `src/json_spod_format_check.py` + `src/consistency_checks.py` | Частично: нет полного SPOD-парсера (структурные правила, `numeric_value_keys`, множественные ошибки) |
| `json_equals_vid` (`enabled=false`) | `json_field_equals_column` | `CONTEST` | `CONTEST_FEATURE.vid == CONTEST_TYPE` | Равенство JSON-значения и колонки | `run_json_field_equals_column()` | `json_field_equals_column` | Зависит от правила (часто `REWARD`) | `src/consistency_checks.py` | Частично: нет `must_not_equal`, `filter_column/filter_value`, `json_filter_key/json_filter_value`; правило выключено |
| `json_in_product` (`enabled=false`) | `json_field_in_column` | `CONTEST` | `CONTEST_FEATURE.vid` в списке `PRODUCT` текущей строки | Принадлежность JSON-значения списку | `run_json_field_in_column()` | `json_field_in_column` | Зависит от правила | `src/consistency_checks.py` | Частично: построчная логика вместо проверки множеств на уровне листа; правило выключено |
| `json_priority_unique` (`enabled=false`) | `json_priority_unique_per_contest_link` | `REWARD` | `REWARD_ADD_DATA.priority` внутри массива одной строки | Повтор `priority` в одной JSON-ячейке | `run_json_priority_unique_per_contest_link()` | `json_priority_unique_per_contest_link` | `REWARD` + `REWARD-LINK` | `src/consistency_checks.py` | Частично: нет группировки по `CONTEST_CODE` и связи с `REWARD-LINK`; правило выключено |

---

## Итог по покрытию

- Полностью/почти полностью совпадают: `csv_columns_count`, `field_length`, базовый `field_format`.
- Частично совпадают (упрощены): `unique`, `referential`, `referential_composite`, `cross_sheet_date_lte_today`, `json_spod_format`, `json_field_equals_column`, `json_field_in_column`, `json_priority_unique_per_contest_link`.
- В текущем `config.json` сейчас выключены: `ref_link_to_reward`, `ref_group_to_contest`, `ref_composite_example`, `json_equals_vid`, `json_in_product`, `json_priority_unique`.

## Почему есть упрощения

Основная причина — архитектурная: в текущем проекте проверки реализованы как облегчённый модуль для пайплайна на `ParsedRow/MergedRow` без полного слоя специализированной бизнес-логики SPOD (в т.ч. без расширенных row-фильтров, межлистовых lookup-параметров и детального SPOD-JSON валидатора).

## Обновление статуса (2026-04-29)

- Движок проверок расширен: добавлена SPOD-совместимость формата правил и алиасов листов.
- Реализованы фильтры строк для `referential`, расширения `unique`, `json_field_equals_column`, `json_field_in_column`, `cross_sheet_date_lte_today`.
- `config.json` обновлён до расширенного набора правил (максимально применимого к текущим сущностям).
- Лист `CONSISTENCY` переработан в сводный формат по правилам и стендам с `sample` (место и причина ошибки).
