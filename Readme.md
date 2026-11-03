# Pereval API

Учебный REST API для заявок о горных перевалах: координаты, сложность по сезонам
и изображения. FastAPI, Pydantic, синхронный SQLAlchemy, PostgreSQL 18 и Alembic.
Упоминание ФСТР относится к учебной постановке, реальное сотрудничество не заявляется.

## Запуск

Python 3.13.9, отдельная PostgreSQL и пустая БД. Из корня проекта в PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-dev.txt
Copy-Item .env.example .env
# Заполнить .env реквизитами локальной БД, не добавлять его в Git.
python -m alembic upgrade head
python -m app.main
```

Swagger: [localhost:8000/docs](http://localhost:8000/docs).
Повторный `alembic upgrade head` безопасен. Схема не создаётся при импорте.
Docker Compose выполняет миграции до запуска приложения.

## Проверки

```powershell
python -m pytest -m "not integration"
python -m pytest -m integration tests/integration
```

Интеграционные тесты требуют Docker и создают одноразовый PostgreSQL.
Проверяются ограничения, миграции, транзакции и конкурентные запросы.
Справочник: [контракт API](docs/API_CONTRACT.md). Нагрузочные показатели не измерялись.

## Доступ и модерация

Все прикладные маршруты требуют Bearer-токен. Автор видит свои заявки и изменяет
только new. Модератор читает очередь, переводит new → pending → accepted/rejected;
для rejected обязательна причина. История доступна владельцу и модератору.

Оператор выдаёт токен через `python -m app.cli issue --email author@example.com
--role author --fam Test --name Author --ttl-hours 1` (одна строка).
Отзыв: `python -m app.cli revoke --token-id <id>`. Не публикуйте выданный секрет.
Схема обновляется `alembic upgrade head`, включая доступ и историю решений.
