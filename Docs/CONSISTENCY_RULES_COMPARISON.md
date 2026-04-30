# Сравнение правил консистентности: текущий проект vs SPOD_PARCE_LOAD

Документ фиксирует сравнение по типам правил из `config.json` (`consistency_checks`) с эталонной логикой `SPOD_PARCE_LOAD`.

## Краткая легенда

- **Текущий проект**: реализация в `src/spod_exporter/consistency_checks.py`.
- **SPOD_PARCE_LOAD**: эталонные проверки и формат правил.
- **Статус**:
  - `Совпадает` — поддержка ключевых параметров и сценариев есть.
  - `Частично` — поддержка есть, но есть упрощения или не все параметры используются.

---

## Сводная таблица по типам правил

| Тип правила | Текущий статус | Что поддержано в текущем проекте | Комментарий по сравнению с SPOD_PARCE_LOAD |
|---|---|---|---|
| `csv_columns_count` | Совпадает | Проверка числа колонок по сущности/листу; поддержка `expected_columns=0` как max по заголовкам; вывод в `CONSISTENCY` и `CC_*` | Соответствует целевому сценарию контроля структуры CSV |
| `unique` | Совпадает | `key_columns`, `unique_scope_mode`, `unique_scope_conditions`, `unique_require_non_empty`; `scope` (`per_stand` / `merged` / `both`) | Базовая и расширенная логика уникальности реализована |
| `field_length` | Совпадает | Проверки длины по полям и условиям правил | Соответствует expected behavior |
| `field_format` | Совпадает | Форматы `date`, `decimal`, `fixed_length_digits`, `allow_empty`, `special_values` | Соответствует практическим правилам проекта |
| `referential` | Совпадает | `sheet_src/sheet_ref`, `column_src/column_ref`, `src_row_conditions`, `ref_row_conditions` | Поддержка row-фильтров добавлена |
| `referential_composite` | Совпадает | `columns_src/columns_ref`, `src_row_conditions`, `ref_row_conditions` | Составные связи поддержаны |
| `cross_sheet_date_lte_today` | Совпадает | Межлистовой lookup: `sheet_src`, `column_src`, `sheet_ref`, `column_ref`, `column_date_ref`, `date_format` | Логика межлистовой проверки реализована |
| `json_spod_format` | Частично | Проверка JSON-формата, нормализация, ключевые валидаторы | Возможны отличия от глубокой доменной валидации эталона в edge-case сценариях |
| `json_field_equals_column` | Совпадает | `json_key`, `column_compare`, `must_not_equal`, фильтры `filter_*`, `json_filter_*` | Расширенные параметры поддержаны |
| `json_field_in_column` | Совпадает | `json_key`, `column_in_sheet` | Реализована проверка вхождения JSON-значения в колонку листа |
| `json_priority_unique_per_contest_link` | Частично | Проверка уникальности приоритета с учетом связей | Бизнес-специфические edge-cases могут отличаться от эталонного проекта |

---

## Что изменилось относительно старого статуса документа

- Ранее ряд правил был отмечен как «упрощённый» из-за отсутствия фильтров строк и расширенных параметров.
- После доработок (расширение `consistency_checks`, обновление `config.json`) эти ограничения сняты для `unique`, `referential`, `referential_composite`, `json_field_equals_column`, `json_field_in_column`, `cross_sheet_date_lte_today`.
- В текущем статусе частичными остаются только JSON-правила, где возможны доменные отличия в редких сценариях.

---

## Актуальный итог по покрытию

- **Совпадает**: `csv_columns_count`, `unique`, `field_length`, `field_format`, `referential`, `referential_composite`, `cross_sheet_date_lte_today`, `json_field_equals_column`, `json_field_in_column`.
- **Частично**: `json_spod_format`, `json_priority_unique_per_contest_link`.

Статус актуализирован по текущей реализации проекта и действующему `config.json`.
