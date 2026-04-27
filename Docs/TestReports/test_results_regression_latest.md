# Отчет по тестированию и регрессии (v2)

## Сводка
- Всего сценариев: 6
- Успешно: 6
- Неуспешно: 0

## Сценарии

### S0_BASELINE
- Статус: PASS
- found_files: 30
- processed_files: 30
- processed_rows: 73346
- same_key_diff: 19

### S0_QUOTE_PRESERVATION
- Статус: PASS
- equal: True
- actual_prefix: "[{"""period_code""": 1, """criterion_mark_type""": """>""", """criterion_mark_value""": 0, """start_dt""": """2023-06-0
- check: raw_parser_preservation

### S1_PRECHECK_MISSING_FILE
- Статус: PASS
- returncode: 1

### S2_NON_OPTIONAL_DIFFERENCE_DETECTED
- Статус: PASS
- group_different: 27

### S3_OPTIONAL_ONE_STAND_IGNORED
- Статус: PASS
- employee_different: 0

### S4_OPTIONAL_TWO_STANDS_CONFLICT
- Статус: PASS
- employee_same_key_diff: 1

## Unit-тесты

- Команда: `python3 -m unittest discover -s src/Tests -p "test_*.py"`
- Результат: `OK`
- Выполнено тестов: `3` (`business_key` для GROUP, fallback-ключ, merge при одинаковом `row_hash` и разном сыром содержимом)