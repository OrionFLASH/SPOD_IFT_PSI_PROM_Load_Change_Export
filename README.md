# SPOD IFT/PROM/PSI Load Change Export

## Формулировка задачи и ТЗ

Программа предназначена для консолидации настроек турниров и наград из трех стендов (`IFT`, `PROM`, `PSI`) на основе CSV-файлов из `IN/SPOD`.

Цели:
- читать 10 типов CSV по каждому стенду;
- объединять однотипные данные в единый набор;
- формировать Excel (1 лист на тип сущности);
- сохранять результаты и историю загрузок в SQLite;
- не допускать повторной загрузки уже обработанных файлов по `SHA-256`;
- управлять путями, именами, ключами и режимами через `config.json`.

## Структура каталогов

- `src` — основной исходный код.
- `src/spod_exporter` — модули пайплайна консолидации (`pipeline`, `consistency_checks`, `config`, `models`, …).
- `src/Tests` — автотесты.
- `IN` — входные файлы.
- `OUT/XLS` — выходные Excel-файлы (сохранение по датам: `YYYY/MM-DD`).
- `OUT/DB` — база SQLite.
- `log` — INFO/DEBUG логи (сохранение по датам: `YYYY/MM-DD`).
- `Docs` — дополнительная документация.
- `.cursor/rules` — правила Cursor для проекта (формат roadmap, язык ответов агента).

## Документация и навигация

| Файл | Содержание |
|------|------------|
| `README.md` (этот файл) | Задача, решение, запуск, переменные/функции, конфиг, **статус реализации**, история версий |
| `Docs/ROADMAP.md` | Единый roadmap проекта: этапы, детальный план 1–20, `consistency_checks` C-01…C-10 и бэклог; статусы `[v]` / `[w]` / `[ ]` / `[x]` |
| `Docs/План_работ_SPOD.md` | Фазы 1–4 по направлениям с таблицами статусов |
| `Docs/ТЗ_SPOD_Консолидация.md` | Техническое задание и чеклист соответствия §7 |
| `Docs/Системные_требования_SPOD.md` | Функциональные и нефункциональные требования, чеклист §6 |
| `Docs/TestReports/test_plan_detailed.md` | Сценарии S0–S4 и статусы прогонов |
| `Docs/TestReports/test_results_regression_latest.md` | Последний отчёт регрессии и unit-тестов |
| `Docs/TestReports/improvement_proposals.md` | Реализованные улучшения и организационный бэклог |
| `.cursor/rules/roadmap-spod-status.mdc` | Маркеры статусов в roadmap: `[v]` / `[w]` / `[ ]` / `[x]` |
| `.cursor/rules/russian-communication.mdc` | Язык общения и статусов в чате — русский (`alwaysApply`) |

## Статус реализации (сводная матрица)

Обозначения: **[сделано]** — в коде и документации закреплено; **[частично]** — есть часть или только ручные проверки; **[не сделано]** — не реализовано.

| Компонент / возможность | Статус |
|-------------------------|--------|
| Сканирование `IN/SPOD`, pre-check всех CSV | **[сделано]** |
| Парсинг `;`, нормализация, `business_key`, `row_hash` | **[сделано]** |
| Дедуп файлов по SHA-256 в SQLite | **[сделано]** |
| Merge между стендами с приоритетом дублей (`PROM` -> `PSI` -> `IFT`) | **[сделано]** |
| Служебные колонки (`source_stands`, `same_row_stands`, `same_key_diff_stands`) | **[сделано]** |
| Экспорт Excel в `OUT/XLS`, форматирование из `config.json` | **[сделано]** |
| Лист `SUMMARY` | **[сделано]** |
| Лист `DIFF_REPORT` (подробные различия дублей по ключам/колонкам/позициям) | **[сделано]** |
| Лист `CONS_REPORT` (агрегированные нарушения консистентности) | **[сделано]** |
| Проверки консистентности (`consistency_checks`), SPOD-совместимые правила, лист `CONSISTENCY`, колонки `CONSIST_*` / `CC_*` | **[сделано]** |
| Логи INFO/DEBUG в `log/` по шаблону имён и формату DEBUG | **[сделано]** |
| Консольная аналитика и профилирование этапов | **[сделано]** |
| Параллельная обработка (`parallel_workers`, `--parallel-workers`) | **[сделано]** |
| Режим `dry-run` (конфиг + `--dry-run`) | **[сделано]** |
| Unit-тесты в `src/Tests` | **[частично]** (13 тестов: пайплайн, `consistency_checks`, при наличии `IN/SPOD` — интеграция GROUP; без полного покрытия Excel/CI) |
| Интеграционные сценарии S0–S4 (ручной/внешний прогон) | **[частично]** (отчёты в `Docs/TestReports/`; не в CI) |
| Валидация схемы CSV (обязательные колонки по сущности) | **[не сделано]** |
| CI (автозапуск тестов) | **[не сделано]** |
| Миграции SQLite | **[не сделано]** |
| Экспорт отчёта в JSON/CSV отдельно от Excel | **[не сделано]** |

## Подробное описание решения

1. **Сканирование входа**
   - Программа проходит по `IN/SPOD/{IFT,PROM,PSI}`.
   - Для каждого типа сущности берет имя файла из `config.json` отдельно по каждому стенду (`entities.<ENTITY>.file_names.<STAND>`).
   - До начала обработки выполняется проверка наличия всех обязательных файлов и каталогов; при ошибках запуск блокируется.
   - Для найденного CSV вычисляет `SHA-256` и размер.

2. **Парсинг и канонизация строк**
   - CSV читается с разделителем `;`.
   - Значения нормализуются (trim и единый формат пустых значений).
   - Для каждой строки строится:
     - `business_key` по правилам сущности из `config.json`;
     - `row_hash` по каноническому JSON строки.

3. **Дедупликация файлов в SQLite**
   - Таблица `ingested_files` имеет уникальный индекс на `file_hash_sha256`.
   - Если hash уже встречался, файл отмечается как `SKIPPED_DUPLICATE` и повторно в БД не пишется.

4. **Объединение дублей по ключу**
   - Группировка выполняется по `business_key`.
   - На лист сущности выводится **одна** строка на каждый ключ.
   - Выбор строки идет по приоритету стендов: `PROM` -> `PSI` -> `IFT`.
   - Если внутри выбранного стенда есть несколько строк с тем же ключом, берется **первая встреченная** строка (минимальный `row_num`).
   - В служебных полях выводятся:
     - `same_row_stands` — стенды, где найдена полностью такая же строка;
     - `same_key_diff_stands` — стенды, где тот же ключ, но строка отличается;
     - `source_stands` дублирует список `same_row_stands` для совместимости.
   - Детализация различий (колонки, позиции и фрагменты) на лист сущности не выводится и переносится в `DIFF_REPORT`.

5. **Экспорт в Excel**
   - Имя файла собирается из `output_name_prefix` + `output_timestamp_format` + `.xlsx`.
   - Листы:
     - `SUMMARY`;
     - по одному листу на каждую сущность (`CONTEST`, `EMPLOYEE`, ...);
    - при `excel.diff_report_sheet.enabled=true` — лист **`DIFF_REPORT`** (детализация дублей по ключам, стендам, колонкам и позициям отличий);
    - при `consistency_checks.enabled=true` — лист **`CONSISTENCY`** (свод по правилам и стендам), лист **`CONS_REPORT`** (агрегированный реестр нарушений), и при необходимости колонки на листах сущностей (`CONSIST_*`, `CC_*` — см. раздел **`consistency_checks`** в конфиге).
    - CSV-экспорт листов включается отдельными флагами:
      - `excel.diff_report_sheet.export_csv` — создание `..._DIFF_REPORT.csv`;
      - `excel.cons_report_sheet.export_csv` — создание `..._CONS_REPORT.csv`.
   - Добавляются служебные колонки (полный перечень задаётся в `config.json`, в т.ч.):
     - `source_stands`;
     - `source_count`;
     - `is_equal_all`;
     - `diff_group_key`;
    - `same_key_diff_values_flag`;
    - `same_key_diff_values_note`;
    - `same_row_stands`;
    - `same_key_diff_stands`;
     - при включённых проверках консистентности — `CONSIST_ROW_DETAIL`, `CONSIST_ALL_STAND_ISSUES` и колонки по правилам (`CC_*`).
   - Применяется форматирование из `config.json`:
     - `freeze_panes`, `auto_filter_header`;
     - автоподбор ширины с ограничением;
     - перенос текста;
     - выравнивание заголовков и данных;
     - подсветка строк по правилам разночтений с приоритетом;
     - подсветка заголовков ключевых полей;
     - рамки данных (горизонталь пунктир, вертикаль точки);
     - разделители (двойная линия под заголовком, толстая вертикальная линия между CSV-блоком и служебными полями).

6. **Консольный отчет процесса**
   - В реальном времени отображаются:
     - старт обработки и `run_id`;
     - сколько и каких файлов найдено;
     - сколько файлов/строк обработано;
     - были ли обновления БД или хэши совпали;
     - сколько найдено совпадений и разночтений;
     - статистика по каждой сущности.
  - После `consistency_checks` дополнительно печатается таблица нарушений:
    - строки: `rule_id` + `rule_type`;
    - колонки: стенды из `stands` (`IFT` / `PROM` / `PSI`) + `NO_STAND` + `TOTAL`;
    - значения: число найденных нарушений по каждому правилу в разрезе стендов.

## Список переменных и функций

### `src/main.py`
- `parse_args() -> argparse.Namespace`
  - Назначение: разбор CLI аргументов (`--config`, `--dry-run`, `--parallel-workers`).
  - Пример: `python3 src/main.py --config config.json --dry-run`.
- `main() -> None`
  - Назначение: точка входа, инициализация конфига, логов и пайплайна; проброс `dry_run` и `parallel_workers` в `config["runtime"]`.
  - Дополнительно: формирует подкаталоги даты `YYYY/MM-DD` для путей логов и Excel.
  - Пример: `python3 src/main.py --config config.json`.

### `src/spod_exporter/config.py`
- `load_config(config_path: Path) -> dict[str, Any]`
  - Назначение: загрузка и базовая валидация `config.json`.
  - Ключевые переменные:
    - `required_paths` — обязательные пути.

### `src/spod_exporter/logging_setup.py`
- `setup_logging(log_dir: Path, topic: str) -> tuple[logging.Logger, Path, Path]`
  - Назначение: настройка INFO/DEBUG логов с шаблоном имен.
  - Возвращает пути к логам.
- `debug_extra(class_name: str, func_name: str) -> dict[str, str]`
  - Назначение: формирование служебных полей для DEBUG-формата.

### `src/spod_exporter/models.py`
- `FileDescriptor`
  - Назначение: метаданные файла (`stand`, `entity`, `file_hash`, ...).
- `ParsedRow`
  - Назначение: канонизированная строка CSV с ключом и hash.
- `MergedRow`
  - Назначение: объединенная строка для Excel/SQLite.

### `src/spod_exporter/pipeline.py`
- Класс `SpodPipeline`
  - Назначение: полный ETL-процесс.
  - Основные поля:
    - `config` — конфигурация;
    - `paths` — рабочие пути;
    - `entities` — карта типов/ключей;
    - `stands` — список стендов;
    - `db_path` — путь к SQLite;
    - `run_id` — идентификатор запуска.

#### Методы `SpodPipeline`
- `run() -> tuple[Path, Path]`
  - Полный запуск: DB -> parse -> merge -> excel.
- `_init_db(conn)`
  - Создание таблиц `runs`, `ingested_files`, `raw_rows`, `merged_rows`.
- `_save_run(conn, status, excel_path)`
  - Фиксация статуса запуска.
- `_scan_files() -> list[FileDescriptor]`
  - Поиск CSV и расчет hash.
- `_parse_all_rows(files, conn) -> dict[str, list[ParsedRow]]`
  - Чтение всех строк и запись raw-данных для новых hash.
- `_is_new_file_hash(cursor, file_hash) -> bool`
  - Проверка уникальности файла.
- `_normalize_row(row) -> dict[str, str]`
  - Канонизация значений строки.
- `_build_business_key(entity, row) -> str`
  - Построение ключа сущности; fallback на `HASH:*`.
- `_hash_json(payload) -> str`
  - SHA-256 канонического JSON.
- `_merge_rows(parsed_rows) -> dict[str, list[MergedRow]]`
  - Объединение строк между стендами.
- `_export_excel(merged) -> Path`
  - Запись Excel-файла с листами и summary.
- `_run_consistency_checks(parsed, merged) -> None`
  - Запуск `consistency_checks` из конфига после merge (профилирование: `consistency_checks` в логах этапов).

## Ключи сущностей (по умолчанию в `config.json`)

- `CONTEST`: `CONTEST_CODE`
- `EMPLOYEE`: `PERSON_NUMBER`
- `GROUP`: `CONTEST_CODE + GROUP_CODE + GROUP_VALUE`
- `INDICATOR`: `CONTEST_CODE + INDICATOR_CODE + N`
- `ORG_UNIT_V20`: `ORG_UNIT_CODE`
- `REPORT`: `MANAGER_PERSON_NUMBER + CONTEST_CODE + TOURNAMENT_CODE + CONTEST_DATE`
- `REWARD`: `REWARD_CODE`
- `REWARD-LINK`: `CONTEST_CODE + GROUP_CODE + REWARD_CODE`
- `SCHEDULE`: `TOURNAMENT_CODE`
- `USER_ROLE`: `RULE_NUM`

## Конфигурация (`config.json`)

Основные разделы:
- `paths` — вход/выход/логи;
- `stands` — список стендов;
- `entities` — `business_key` и `file_names` по каждому стенду для каждой сущности;
- `entities.optional_fields` — правила обработки доп.полей относительно эталонного стенда;
- `entities` — для каждого листа также задается `excel_sheet` (`freeze_panes`, `auto_filter_header`);
- `excel` — параметры имени выходного файла (`output_name_prefix`, `output_timestamp_format`);
  - `summary_sheet` — закрепление и автофильтр для `SUMMARY`;
  - `consistency_sheet` — закрепление и автофильтр для `CONSISTENCY`;
  - `diff_report_sheet` — включение листа `DIFF_REPORT`, имя листа, закрепление, длины контекста для сниппетов, флаг `export_csv` (экспорт `DIFF_REPORT` в CSV);
  - `cons_report_sheet` — настройки листа `CONS_REPORT`, включая флаг `export_csv` (экспорт `CONS_REPORT` в CSV);
  - `formatting_defaults` — автоподбор ширины, лимит, перенос, выравнивание и цвета подсветки;
  - `formatting_defaults.borders` — параметры рамок и разделителей;
  - `formatting_defaults.extra_header_fill` — цвет заголовков доп.полей (зеленый).
- `sqlite` — имя БД и режим дедупликации;
- `merge` — правила нормализации/сравнения;
  - при выборе дублей используется фиксированный приоритет стендов `PROM -> PSI -> IFT`; внутри стенда берется первая строка по `row_num`;
  - `reference_row_stand` сохраняется в конфиге для совместимости с существующими проверками/настройками;
  - `source_stands`/`same_row_stands` — стенды с полностью совпавшей выбранной строкой; `same_key_diff_stands` — стенды с тем же ключом и другими значениями.
- `logging` — тема логирования;
- `runtime` — режим выполнения: `dry_run`, `parallel_workers` (`"auto"` или число), при необходимости `fail_fast`, `max_errors`.
- **`consistency_checks`** (по образцу [SPOD_PARCE_LOAD](https://github.com/OrionFLASH/SPOD_PARCE_LOAD)) — построчные и сводные проверки после merge:
  - `enabled` — включить этап (`false` — выключить); **если ключа нет, а секция `consistency_checks` непустая, проверки считаются включёнными** (чтобы не отключались молча при забытых `rules`); `fail_fast` — прервать запуск при первой ошибке с `severity=error`;
  - `summary_sheet_name` — имя листа сводки (по умолчанию `CONSISTENCY`);
  - поддерживается формат правил SPOD (`sheet`, `sheet_src`, `sheet_ref`, `column_src`, `column_ref`, `columns_src`, `columns_ref`, `json_key`, `column_compare`, `format.date_format`);
  - **`csv_columns_count`** — ожидаемое число колонок по сущности (`entities.<ENTITY>.expected_columns`, значение **`0`** = эталон как максимум числа колонок заголовка по стендам); блок **`output.column_suffix_per_stand`** — имя колонки статуса на листе сущности;
  - **`rules`** — массив правил с полями `id`, `type`, `entity`, `enabled`, **`scope`**: `per_stand` | `merged` | `both` (при `both` проверка выполняется и по сырым строкам стенда, и по merged; в Excel могут быть две колонки через `output.column_suffix_per_stand` / `column_suffix_merged`);
  - поддерживаемые **`type`**: `unique`, `field_length`, `field_format` (подтипы `date`, `decimal`, `fixed_length_digits`), `referential`, `referential_composite`, `cross_sheet_date_lte_today`, `json_spod_format`, `json_field_equals_column`, `json_field_in_column`, `json_priority_unique_per_contest_link`.
  - `field_format.decimal` поддерживает `decimal_places`; `date` — `special_values` и `allow_empty`.
  - `referential` / `referential_composite` поддерживают `src_row_conditions` / `ref_row_conditions`.
  - `json_field_equals_column` поддерживает `must_not_equal`, `filter_column/filter_value`, `json_filter_key/json_filter_value`.
  - `json_field_in_column` поддерживает `column_in_sheet` (проверка значения JSON-ключа по множеству значений колонки листа).

### `src/spod_exporter/consistency_checks.py`

- `execute_consistency_checks(...)` — запуск правил; дополняет `MergedRow.merged_data` агрегатами по ключу.
- `append_consistency_sheet(workbook, cc, violations, stands)` — добавляет лист сводки в книгу Excel:
  - формат листа `CONSISTENCY`: `ТИП ПРОВЕРКИ`, `Описание`, `таблица источник`, `поле источник`, `таблица где проверяем`, `поле для проверки`, `параметр сравнения`, `комментарий`, `check_id`, `rule_type`, `scope`, `stand`, `total_rows`, `violations`, `sample`;
  - `sample` включает контекст места ошибки: стенд, строка, business_key и сообщение.
  - к листу `CONSISTENCY` применяется то же общее форматирование, что и к другим листам (`_format_sheet`): границы, выравнивание, перенос и автоподбор ширины колонок с ограничением `excel.formatting_defaults.max_column_width` (по умолчанию `150`).

## Формат логирования

- INFO-файл: `INFO_<topic>_YYYYMMDD_HH.log`
- DEBUG-файл: `DEBUG_<topic>_YYYYMMDD_HH.log`

DEBUG-строка:

`дата время - [уровень] - сообщение [class: <имя класса> | def: <имя функции>]`

## Запуск

1. Создать виртуальное окружение:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
2. Убедиться, что `openpyxl` доступен в окружении (в вашем контуре без `pip install` пакет должен быть предустановлен).
3. Запуск:
   - базовый: `python3 src/main.py --config config.json`
   - dry-run (без записи raw/merged и без сохранения Excel): `python3 src/main.py --config config.json --dry-run`
   - явное число потоков: `python3 src/main.py --config config.json --parallel-workers 4`

По умолчанию число потоков определяется автоматически от количества ядер CPU.

## Тесты

- Запуск unit-тестов: `python3 -m unittest discover -s src/Tests -p "test_*.py"`
- Файлы: `src/Tests/test_pipeline.py` (`TestSpodPipeline`), `src/Tests/test_consistency_checks.py` (`TestConsistencyChecks`), при наличии `IN/SPOD` — `src/Tests/test_consistency_real_group.py` (дубликаты GROUP, контест `01_2026-0_05-2_4`).
- Покрытие на текущий момент:
  - **[сделано]** формирование `business_key` для сущности `GROUP`;
  - **[сделано]** fallback-ключ `HASH:*` при пустых полях ключа для `CONTEST`;
  - **[сделано]** выбор одной строки по ключу с приоритетом стендов `PROM -> PSI -> IFT` и первой строкой внутри стенда;
  - **[сделано]** базовые сценарии `consistency_checks` (число колонок, уникальность, формат, ссылка, `fail_fast`, флаг `enabled`, выравнивание `business_key` при unique);
  - **[сделано]** при наличии входных CSV — интеграционный сценарий дублей GROUP (`test_consistency_real_group.py`, контест `01_2026-0_05-2_4`).
- Интеграционные и регрессионные сценарии (S0–S4 и заметки по консистентности) в `Docs/TestReports/test_plan_detailed.md`; последние результаты — в `Docs/TestReports/test_results_regression_latest.md`.

## История версий

### v0.2.0 (текущая ветка развития)

- **[сделано]** Проверки консистентности: раздел `consistency_checks` в `config.json`, модуль `consistency_checks.py`, лист **`CONSISTENCY`**, колонки на листах сущностей, интеграция в пайплайн; включение по умолчанию при непустой секции без ключа `enabled` (`is_consistency_checks_enabled`); строка-итог на `CONSISTENCY` при нуле нарушений; правило **`uniq_group_key`** для GROUP (`scope: both`); выравнивание `business_key` в нарушениях unique с `ParsedRow`.
- **[сделано]** SPOD-совместимость правил консистентности: нормализация схемы правил (`sheet_*`, `column_*`, `json_key`, `format`) и расширение типов проверок.
- **[сделано]** Новый формат листа `CONSISTENCY`: свод по правилам в разрезе стендов (`IFT/PROM/PSI` + `NO_STAND`) с колонкой `sample` и деталями места ошибки.
- **[сделано]** Для листа `CONSISTENCY` включено общее форматирование Excel (границы, перенос, автоширина с лимитом `150`) и конфиг `excel.consistency_sheet`.
- **[сделано]** Консольная таблица после консистентности: подсчёт нарушений по `rule_id`/стендам.
- **[сделано]** Входные CSV в `IN/SPOD/IFT` и `IN/SPOD/PSI` переименованы с `(PROM)` на `(IFT)` / `(PSI)`; `config.json` синхронизирован с новыми именами файлов.
- **[сделано]** Лист Excel `DIFF_REPORT` и настройка `excel.diff_report_sheet` в `config.json`.
- **[сделано]** Детализация дублей перенесена в `DIFF_REPORT` (включая колонки, позиции и сниппеты отличий), а на листах сущностей добавлены `same_row_stands` и `same_key_diff_stands`.
- **[сделано]** Добавлен лист `CONS_REPORT` с агрегированными нарушениями консистентности.
- **[сделано]** Добавлены управляемые флаги CSV-экспорта для отчетных листов: `excel.diff_report_sheet.export_csv` и `excel.cons_report_sheet.export_csv` (по умолчанию выключены).
- **[сделано]** Сохранение логов и Excel в подкаталоги по дате: `YYYY/MM-DD`.
- **[сделано]** Параллельная обработка файлов/сущностей с авто-числом потоков или явным `--parallel-workers`.
- **[сделано]** Режим `dry-run` в `runtime` и через CLI `--dry-run`.
- **[сделано]** Пакетная запись в SQLite, профилирование этапов в логах.
- **[частично]** Unit/интеграционные тесты расширены (`test_consistency_*`, `test_consistency_real_group` при наличии `IN/SPOD`); полное покрытие и CI — в плане.
- **[сделано]** Документация: README, ТЗ, системные требования, план работ; roadmap — статусы **`[v]`** / **`[w]`** / **`[ ]`** / **`[x]`** по `.cursor/rules/roadmap-spod-status.mdc`.
- **[сделано]** Правило Cursor `.cursor/rules/russian-communication.mdc` — русский язык для ответов и статусов в агенте; навигация по правилам добавлена в README.

### v0.1.0

- Реализован базовый ETL-каркас.
- Добавлены:
  - чтение CSV из 3 стендов;
  - дедупликация файлов по `SHA-256` в SQLite;
  - объединение строк по `business_key + row_hash`;
  - экспорт Excel с 10 листами сущностей и `SUMMARY`;
  - конфигурирование через `config.json`;
  - двухуровневое логирование INFO/DEBUG;
  - первичные unit-тесты.

### Решённые задачи ранних этапов

- Формализация ключей сравнения по типам файлов.
- Проектирование схемы БД для повторных запусков.
- Подготовка управляемого конфига без хардкода путей.
- Настройка трассируемого формата логов.

