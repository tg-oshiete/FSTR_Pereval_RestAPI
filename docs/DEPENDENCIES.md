# Аудит безопасности зависимостей

Срез: 3 ноября 2026 года, Python 3.13.9, pip-audit 2.10.1, источник PyPI.

**QA-01 / P1, открыт.** Команда возвращает код 1: 36 записей в 10 пакетах,
20 различных идентификаторов advisory после удаления повторов. Повторы в ответе
источника не означают разные уязвимости. Приёмка всех обязательных jobs заблокирована.

Воспроизведение после установки обоих lock-файлов:

```powershell
New-Item -ItemType Directory -Force reports | Out-Null
python -m pip_audit --disable-pip --no-deps -r requirements.txt -r requirements-dev.txt --format json --output reports/pip-audit.json
```

Полный машинный отчёт остаётся в `reports/pip-audit.json` и публикуется как
артефакт CI даже при провале проверки. Ошибки сети/источника тоже не дают зелёный job.
Ни `--ignore-vuln`, ни автоматического `--fix`, ни `continue-on-error` нет.

## Найденные уязвимости

| Пакет / установленная версия | Advisory | Исправленные версии по источнику | Область |
| --- | --- | --- | --- |
| `anyio==4.12.1` | [GHSA-82r6-8w77-94w6](https://github.com/advisories/GHSA-82r6-8w77-94w6) (`PYSEC-2026-4025`) | 4.14.2 | Runtime-зависимости |
| `anyio==4.12.1` | [GHSA-5p39-cfhj-2xmp](https://github.com/advisories/GHSA-5p39-cfhj-2xmp) (`PYSEC-2026-4024`) | 4.14.2 | Runtime-зависимости |
| `dnspython==2.8.0` | [GHSA-45c3-73f7-pv4m](https://github.com/advisories/GHSA-45c3-73f7-pv4m) (`CVE-2026-114605`) | 2.9.0 | Runtime-зависимости |
| `idna==3.11` | [GHSA-65pc-fj4g-8rjx](https://github.com/advisories/GHSA-65pc-fj4g-8rjx) (`PYSEC-2026-215`) | 3.15 | Runtime-зависимости |
| `pydantic-settings==2.14.0` | [GHSA-4xgf-cpjx-pc3j](https://github.com/advisories/GHSA-4xgf-cpjx-pc3j) (`CVE-2026-58203`) | 2.14.2 | Runtime-зависимости |
| `python-dotenv==1.2.1` | [GHSA-mf9w-mj56-hr94](https://github.com/advisories/GHSA-mf9w-mj56-hr94) (`PYSEC-2026-2270`) | 1.2.2 | Runtime-зависимости |
| `starlette==0.50.0` | [GHSA-86qp-5c8j-p5mr](https://github.com/advisories/GHSA-86qp-5c8j-p5mr) (`PYSEC-2026-161`) | 1.0.1 | HTTP-стек и совместимость |
| `starlette==0.50.0` | [GHSA-wqp7-x3pw-xc5r](https://github.com/advisories/GHSA-wqp7-x3pw-xc5r) (`PYSEC-2026-2281`) | 1.1.0 | HTTP-стек и совместимость |
| `starlette==0.50.0` | [GHSA-x746-7m8f-x49c](https://github.com/advisories/GHSA-x746-7m8f-x49c) (`PYSEC-2026-2280`) | 1.1.0 | HTTP-стек и совместимость |
| `starlette==0.50.0` | [GHSA-82w8-qh3p-5jfq](https://github.com/advisories/GHSA-82w8-qh3p-5jfq) (`PYSEC-2026-249`) | 1.3.1 | HTTP-стек и совместимость |
| `starlette==0.50.0` | [GHSA-jp82-jpqv-5vv3](https://github.com/advisories/GHSA-jp82-jpqv-5vv3) (`PYSEC-2026-248`) | 1.3.0 | HTTP-стек и совместимость |
| `certifi==2026.1.4` | [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) (`PYSEC-2026-4320`) | 2026.10.18 | Dev-зависимости |
| `pygments==2.19.2` | [GHSA-5239-wwwm-4pmq](https://github.com/advisories/GHSA-5239-wwwm-4pmq) (`PYSEC-2026-2987`) | 2.20.0 | Dev-зависимости |
| `pytest==9.0.2` | [GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g) (`PYSEC-2026-1845`) | 9.0.3 | Dev-зависимости |
| `pip==25.2` | [GHSA-4xh5-x5gv-qwph](https://github.com/advisories/GHSA-4xh5-x5gv-qwph) (`PYSEC-2026-1795`) | 25.3 | Dev-зависимости |
| `pip==25.2` | [GHSA-6vgw-5pg2-w6jp](https://github.com/advisories/GHSA-6vgw-5pg2-w6jp) (`PYSEC-2026-1796`) | 26.0 | Dev-зависимости |
| `pip==25.2` | [GHSA-58qw-9mgm-455v](https://github.com/advisories/GHSA-58qw-9mgm-455v) (`PYSEC-2026-2875`) | 26.1 | Dev-зависимости |
| `pip==25.2` | [GHSA-jp4c-xjxw-mgf9](https://github.com/advisories/GHSA-jp4c-xjxw-mgf9) (`PYSEC-2026-2876`) | 26.1 | Dev-зависимости |
| `pip==25.2` | [GHSA-wf93-45jw-7689](https://github.com/advisories/GHSA-wf93-45jw-7689) (`PYSEC-2026-196`) | 26.1.2 | Dev-зависимости |
| `pip==25.2` | [GHSA-qwm4-qh6w-59xr](https://github.com/advisories/GHSA-qwm4-qh6w-59xr) (`PYSEC-2026-3721`) | 26.2.0 | Dev-зависимости |

## Ограничение совместимости

`fastapi==0.128.0` объявляет `starlette>=0.40.0,<0.51.0` (проверено через
`importlib.metadata.requires("fastapi")`). Исправления найденных Starlette advisory
начинаются с 1.x; простая замена транзитивного pin нарушит ограничение FastAPI.
Необходимо выбрать совместимую пару FastAPI/Starlette и проверить
валидацию, ошибки, OpenAPI, middleware и авторизацию. Также необходимо согласовать
остальные точечные обновления и пересобрать lock-файлы через pip-tools.

Наличие advisory у пакета не доказывает эксплуатацию в этом API. Например,
часть находок касается form/static/TLS/локальных установщиков. Отдельной
эксплуатационной оценки каждого пути здесь нет; исключения не назначены.
Обязательная проверка наличия известных уязвимостей остаётся блокирующей.

После исправления: повторить аудит обоих lock-файлов, чистую установку и
`pip check`, Ruff, быстрые и PostgreSQL-тесты, сборку и контейнерный HTTP smoke.
Для изменений HTTP-стека отдельно повторить `tests/integration/test_http_blackbox.py`
и `tests/integration/test_access_moderation.py`. Затем запустить удалённый CI.
При аудите массовое обновление зависимостей не выполнялось.
