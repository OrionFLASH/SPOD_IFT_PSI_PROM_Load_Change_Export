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
- `OUT/CSV` — выходные Excel-файлы.
- `OUT/DB` — база SQLite.
- `log` — INFO/DEBUG логи.
- `Docs` — дополнительная документация.

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

5. **Экспорт в Excel**
   - Создается файл `SPOD_Export_YYYYMMDD_HHMMSS.xlsx`.
   - Листы:
     - `SUMMARY`;
     - по одному листу на каждую сущность (`CONTEST`, `EMPLOYEE`, ...).
   - Добавляются служебные колонки:
     - `source_stands`;
     - `source_count`;
     - `is_equal_all`;
     - `diff_group_key`.
     - `same_key_diff_values_flag`;
     - `same_key_diff_values_note`.
   - Применяется форматирование из `config.json`:
     - `freeze_panes`, `auto_filter_header`;
     - автоподбор ширины с ограничением;
     - перенос текста;
     - выравнивание заголовков и данных;
     - подсветка строк по правилам разночтений.

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
  - Назначение: разбор CLI аргументов.
  - Пример: запуск с `--config config.json`.
- `main() -> None`
  - Назначение: точка входа, инициализация конфига, логов и пайплайна.
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
- `entities` — для каждого листа также задается `excel_sheet` (`freeze_panes`, `auto_filter_header`);
- `excel` — шаблон имени выходного файла;
  - `formatting_defaults` — автоподбор ширины, лимит, перенос, выравнивание и цвета подсветки;
- `sqlite` — имя БД и режим дедупликации;
- `merge` — правила нормализации/сравнения;
- `logging` — тема логирования;
- `runtime` — режим выполнения.

## Формат логирования

- INFO-файл: `INFO_<topic>_YYYYMMDD_HH.log`
- DEBUG-файл: `DEBUG_<topic>_YYYYMMDD_HH.log`

DEBUG-строка:

`дата время - [уровень] - сообщение [class: <имя класса> | def: <имя функции>]`

## Запуск

1. Создать виртуальное окружение:
   - `python3 -m venv .venv`
   - `source .venv/bin/activate`
2. Установить зависимости:
   - `pip install -r requirements.txt`
3. Запуск:
   - `python3 src/main.py --config config.json`

## Тесты

- Запуск: `python3 -m unittest discover -s src/Tests -p "test_*.py"`
- Текущие тесты покрывают:
  - формирование `business_key`;
  - fallback-ключ через hash.

## История версий

### v0.1.0
- Реализован базовый ETL-каркас.
- Добавлены:
  - чтение CSV из 3 стендов;
  - дедупликация файлов по `SHA-256` в SQLite;
  - объединение строк по `business_key + row_hash`;
  - экспорт Excel с 10 листами и `SUMMARY`;
  - конфигурирование через `config.json`;
  - двухуровневое логирование INFO/DEBUG;
  - первичные unit-тесты.

### Решенные задачи этапа
- Формализация ключей сравнения по типам файлов.
- Проектирование схемы БД для повторных запусков.
- Подготовка управляемого конфига без хардкода путей.
- Настройка трассируемого формата логов.

