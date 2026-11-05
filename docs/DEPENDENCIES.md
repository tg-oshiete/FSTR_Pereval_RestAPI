# Аудит безопасности зависимостей

**QA-01 / P1 закрыта 5 ноября 2026 года.** Обновлены библиотеки с известными
уязвимостями, совместимость подтверждена тестами и контейнерным запуском. Проверка исходных
lock-файлов 5 ноября 2026 года повторно дала 36 advisory-записей в 10 пакетах
(20 уникальных идентификаторов). После обновления аудит обоих lock-файлов
возвращает код 0: **известных уязвимостей не найдено**, исключения не добавлялись.
Итоговые проверки совместимости приведены в [отчёте](VERIFICATION.md).

## Обновлённые версии

| Пакет | Было | Стало |
| --- | --- | --- |
| FastAPI | 0.128.0 | 0.133.1 |
| Starlette | 0.50.0 | 1.3.1 |
| AnyIO | 4.12.1 | 4.14.2 |
| dnspython | 2.8.0 | 2.9.0 |
| idna | 3.11 | 3.18 |
| pydantic-settings | 2.14.0 | 2.14.2 |
| python-dotenv | 1.2.1 | 1.2.2 |
| certifi | 2026.1.4 | 2026.10.18 |
| Pygments | 2.19.2 | 2.20.0 |
| pytest | 9.0.2 | 9.0.3 |
| pip | 25.2 | 26.2 |
| pip-tools | 7.5.2 | 7.6.2 |

Версии выбранных пакетов проверены по метаданным PyPI и итоговым аудитом полного
набора. Остальные прямые runtime-зависимости, в том числе SQLAlchemy, psycopg2,
Pydantic и Uvicorn, сохранены. Новые минимальные версии зависимостей явно указаны
в `.in`, точные версии полного набора фиксируются в `.txt`.

## Совместимость FastAPI и Starlette

Прежний FastAPI требовал `starlette>=0.40.0,<0.51.0`, поэтому обновить только
Starlette было нельзя. FastAPI 0.133.1 допускает Starlette 1.x: поддержка добавлена
в [FastAPI 0.133.0](https://fastapi.tiangolo.com/release-notes/#01330).
В проекте проверяется конкретная пара **FastAPI 0.133.1 / Starlette 1.3.1**.
Исправления обработки форм вошли в
[Starlette 1.3.1](https://starlette.dev/release-notes/#131-june-12-2026).

Starlette также перевела TestClient на HTTPX2. С прежним HTTPX импорт выводил
предупреждение, нарушая проверку отсутствия побочных эффектов. Dev-зависимость
заменена на `httpx2==2.12.0`, сетевые тесты используют тот же клиент. Вместе с ним
устанавливаются `httpcore2==2.12.0` и `truststore==0.10.5`; для совместимости idna
поднята до 3.18. Предупреждения не подавляются, проверки не ослаблены.
[Документация TestClient](https://starlette.dev/testclient/).

Runtime-код, API-контракт и миграции не изменены. HTTPX2 остаётся только в
окружении разработки; контейнерный smoke проверяет отсутствие dev-пакетов в API-образе.

## Установщик в Docker

Базовый `python:3.13.9-slim` содержал pip 25.2. Dockerfile обновляет установщик до
26.2 и на этапе сборки wheels, и в итоговом образе. Wheel установщика переносится
между стадиями, финальная установка использует `--no-index` и локальные wheels.
Python и версия PostgreSQL сохранены. Это аудит Python-пакетов, не системных
пакетов Debian и не оценка всех возможных уязвимостей инфраструктуры.

## Воспроизведение

После установки runtime- и dev-lock-файлов в новое окружение:

```powershell
python -m pip check
New-Item -ItemType Directory -Force reports | Out-Null
python -m pip_audit --disable-pip --no-deps -r requirements.txt -r requirements-dev.txt --format json --output reports/pip-audit.json
python -m pip_audit --local --format json --output reports/environment-audit.json
```

Первая команда проверяет объявленную совместимость пакетов, следующие — известные
уязвимости в lock-файлах и реально установленном окружении. Ожидаемый результат
аудита — код 0 и `No known vulnerabilities found`. `--ignore-vuln`, `--fix`,
`continue-on-error` и исключений advisory нет. Ошибка сети не считается успехом.

Пересборка lock-файлов после согласованного изменения `.in`:

```powershell
python -m piptools compile --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host --output-file requirements.txt requirements.in
python -m piptools compile --strip-extras --allow-unsafe --no-emit-index-url --no-emit-trusted-host --output-file requirements-dev.txt requirements-dev.in
```

Повторная компиляция текущего набора даёт те же файлы. Для выборочного обновления
нужного транзитивного пакета добавьте `--upgrade-package <имя>`;
[правила обновления pip-tools](https://pip-tools.pypa.io/en/stable/#updating-requirements).
После изменения пакетов обязательны чистая установка, тесты, Docker smoke и аудит.
Команды перечислены в [README](../Readme.md#проверки-qa-и-ci).

Отчёты аудита сохраняются в `reports/` локально и как артефакты CI. Нулевой результат
означает отсутствие **известных источнику на момент проверки** уязвимостей, а не
гарантию абсолютной безопасности. Проверка остаётся обязательной в GitHub Actions.
