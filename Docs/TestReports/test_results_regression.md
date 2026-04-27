# Отчет по тестированию и регрессии

## Сводка

- Всего сценариев: 6
- Успешно: 5
- Неуспешно: 1

## Детализация сценариев

### S0_BASELINE
- Статус: PASS
- found_files: 30
- processed_files: 30
- processed_rows: 73346
- differences: 162
- same_key_diff: 19
- excel: OUT/XLS/SPOD_Export_20260427_235633.xlsx

### S0_QUOTE_PRESERVATION
- Статус: FAIL
- expected_prefix: 
- actual_prefix: 
- equal: False

### S1_PRECHECK_MISSING_FILE
- Статус: PASS
- returncode: 1
- contains_expected_error: True

### S2_NON_OPTIONAL_DIFFERENCE_DETECTED
- Статус: PASS
- returncode: 0
- same_key_diff: 19

### S3_OPTIONAL_FIELD_ONE_STAND_IGNORED
- Статус: PASS
- returncode: 0
- employee_different: 0
- employee_same_key_diff: 0

### S4_OPTIONAL_FIELD_TWO_STANDS_CONFLICT
- Статус: PASS
- returncode: 0
- employee_different: 2
- employee_same_key_diff: 1
