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
- `src/spod_exporter` — модули пайплайна консолидации.
- `src/Tests` — автотесты.
- `IN` — входные файлы.
- `OUT/XLS` — выходные Excel-файлы.
- `OUT/DB` — база SQLite.
- `log` — INFO/DEBUG логи.
- `Docs` — дополнительная документация.
- `.cursor/rules` — правила Cursor для проекта (формат roadmap, язык ответов агента).

## Документация и навигация

| Файл | Содержание |
|------|------------|
| `README.md` (этот файл) | Задача, решение, запуск, переменные/функции, конфиг, **статус реализации**, история версий |
| `Docs/Roadmap_SPOD.md` | Этапы 0–9 и бэклог; статусы `[v]` / `[w]` / `[ ]` / `[x]` (см. `.cursor/rules/roadmap-spod-status.mdc`) |
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
| Merge между стендами, optional-поля, `reference_row_stand` | **[сделано]** |
| Служебные колонки (`source_stands`, diff-флаги и т.д.) | **[сделано]** |
| Экспорт Excel в `OUT/XLS`, форматирование из `config.json` | **[сделано]** |
| Лист `SUMMARY` | **[сделано]** |
| Лист `DIFF_REPORT` (`excel.diff_report_sheet`) | **[сделано]** |
| Логи INFO/DEBUG в `log/` по шаблону имён и формату DEBUG | **[сделано]** |
| Консольная аналитика и профилирование этапов | **[сделано]** |
| Параллельная обработка (`parallel_workers`, `--parallel-workers`) | **[сделано]** |
| Режим `dry-run` (конфиг + `--dry-run`) | **[сделано]** |
| Unit-тесты в `src/Tests` | **[частично]** (3 теста: ключи GROUP, fallback CONTEST, merge при коллизии hash) |
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

4. **Объединение между стендами**
   - Группировка: `(business_key, row_hash)`.
   - Если одинаковые строки есть в нескольких стендах, формируется 1 запись с `source_stands`, например `PROM-IFT-PSI`.
   - Если различаются, формируются отдельные записи с меткой конкретного источника.
   - Для сущностей с `optional_fields.enabled=true`:
     - эталон по полям берется из `reference_stand` (сейчас `PROM`);
     - отсутствие доп.поля в одном файле не считается отличием;
     - если доп.поле присутствует в двух сравниваемых файлах, его значения участвуют в сравнении.

5. **Экспорт в Excel**
   - Имя файла собирается из `output_name_prefix` + `output_timestamp_format` + `.xlsx`.
   - Листы:
     - `SUMMARY`;
     - по одному листу на каждую сущность (`CONTEST`, `EMPLOYEE`, ...);
     - при `excel.diff_report_sheet.enabled=true` — лист **`DIFF_REPORT`** (сводка конфликтов).
   - Добавляются служебные колонки (полный перечень задаётся в `config.json`, в т.ч.):
     - `source_stands`;
     - `source_count`;
     - `is_equal_all`;
     - `diff_group_key`;
     - `same_key_diff_values_flag`;
     - `same_key_diff_values_note`;
     - при необходимости — `diff_columns`, `diff_positions`, `diff_snippets`.
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

## Список переменных и функций

### `src/main.py`
- `parse_args() -> argparse.Namespace`
  - Назначение: разбор CLI аргументов (`--config`, `--dry-run`, `--parallel-workers`).
  - Пример: `python3 src/main.py --config config.json --dry-run`.
- `main() -> None`
  - Назначение: точка входа, инициализация конфига, логов и пайплайна; проброс `dry_run` и `parallel_workers` в `config["runtime"]`.
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
  - `diff_report_sheet` — включение листа `DIFF_REPORT`, имя листа, закрепление, длины контекста для сниппетов;
  - `formatting_defaults` — автоподбор ширины, лимит, перенос, выравнивание и цвета подсветки;
  - `formatting_defaults.borders` — параметры рамок и разделителей;
  - `formatting_defaults.extra_header_fill` — цвет заголовков доп.полей (зеленый).
- `sqlite` — имя БД и режим дедупликации;
- `merge` — правила нормализации/сравнения;
  - `reference_row_stand` (по умолчанию `PROM`) — при объединении строк с одинаковым содержимым в выводе подставляются значения полей с этого стенда; список `source_stands` строится только по стендам с **идентичным сырым** набором полей и значений (при коллизии `row_hash` строки с разным сыром разделяются).
- `logging` — тема логирования;
- `runtime` — режим выполнения: `dry_run`, `parallel_workers` (`"auto"` или число), при необходимости `fail_fast`, `max_errors`.

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
- Файл: `src/Tests/test_pipeline.py` (класс `TestSpodPipeline`).
- Покрытие на текущий момент:
  - **[сделано]** формирование `business_key` для сущности `GROUP`;
  - **[сделано]** fallback-ключ `HASH:*` при пустых полях ключа для `CONTEST`;
  - **[сделано]** объединение `GROUP` при одинаковом `row_hash` и разном сыром содержимом (две строки, эталон значений — `PROM`).
- Интеграционные и регрессионные сценарии (S0–S4) описаны в `Docs/TestReports/test_plan_detailed.md`; последние результаты — в `Docs/TestReports/test_results_regression_latest.md`.

## История версий

### v0.2.0 (текущая ветка развития)

- **[сделано]** Лист Excel `DIFF_REPORT` и настройка `excel.diff_report_sheet` в `config.json`.
- **[сделано]** Расширенный набор служебных полей (`diff_columns`, `diff_positions`, `diff_snippets` и др. по конфигу).
- **[сделано]** Параллельная обработка файлов/сущностей с авто-числом потоков или явным `--parallel-workers`.
- **[сделано]** Режим `dry-run` в `runtime` и через CLI `--dry-run`.
- **[сделано]** Пакетная запись в SQLite, профилирование этапов в логах.
- **[частично]** Unit-тесты расширены сценарием merge при коллизии hash (всего 3 теста); интеграционные прогоны задокументированы в `Docs/TestReports/`.
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

