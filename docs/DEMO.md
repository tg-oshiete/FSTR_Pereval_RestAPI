# Показ проекта за 3–5 минут

Сначала выполните [быстрый запуск](../Readme.md#быстрый-запуск-демо). Сборка образа
и загрузка зависимостей не входят в время выступления. Откройте Swagger и терминал.
Для показа запускайте:

```powershell
docker compose --env-file .env.demo -p pereval-demo -f docker-compose.yml -f docker-compose.demo.yml exec app python -m app.demo run --pause
```

Без `--pause` и с `exec -T` тот же сценарий выполняется автоматически и возвращает
код 0 только после всех проверок; код 1 означает ошибку. Все запросы идут по TCP
к настоящему API и PostgreSQL. Секреты в вывод не включаются.

| Время | Что сделать и объяснить | Наблюдаемый результат |
| --- | --- | --- |
| 0:00–0:30 | Назвать учебную задачу, стек и роли; показать Swagger | Семь прикладных операций, Bearer, схемы ошибок |
| 0:30–1:00 | Шаг 1: автор отправляет заявку с PNG | POST 201, получен ID; имя/координаты вымышлены |
| 1:00–1:40 | Шаг 2: изменить только высоту и весенний сезон | PATCH 200; GET сохраняет координаты, лето и фото |
| 1:40–2:10 | Шаг 3: попытка чужого автора; пустое название | 403 FORBIDDEN; 422 VALIDATION_ERROR |
| 2:10–2:50 | Шаг 4: модератор берёт в работу и принимает | new → pending → accepted; два ответа 200 |
| 2:50–3:20 | Шаг 5: открыть историю от имени автора | Два перехода, ID/роль модератора, время UTC |
| 3:20–4:10 | Шаг 6: попытаться изменить принятую заявку | 409 PEREVAL_NOT_EDITABLE; GET доказывает неизменность; без токена 401 |
| 4:10–5:00 | Объяснить транзакцию/блокировку и назвать ограничения | DEMO OK, временные токены отозваны; QA-01 открыт |

Код сценария: [app/demo.py](../app/demo.py). При каждом `run` добавляется новая
принятая заявка и два решения; это намеренно не идемпотентный сценарий. Коллекция
seed при этом не изменяется. Остановка в середине оставляет уже подтверждённые
HTTP-запросы в БД; следующий запуск использует новую заявку.

## Seed и тестовые данные

`seed` создаёт четыре вымышленные личности:

| Email | Назначение |
| --- | --- |
| demo.collection@example.com | Владелец исходной коллекции четырёх статусов |
| demo.author@example.com | Автор новых заявок во время показа |
| demo.other@example.com | Чужой автор для проверки отказа |
| demo.moderator@example.com | Личность модератора; роль назначается временному токену |

Телефоны не добавляются. Все адреса — примеры `example.com`, письма не отправляются.
Координаты и названия не подтверждают существования маршрутов. PNG 16×16 — одноцветный
тестовый файл, создаваемый Pillow из кода; это не фотография и не сторонний материал.
Он проходит ту же валидацию `PerevalCreate`, что и HTTP-запросы. Файлы из интернета
для seed не требуются.

Первый seed атомарно создаёт пользователей, четыре заявки, координаты, фото и пять
решений. Повторный, в том числе одновременный, seed возвращает те же ID, не добавляет
строки и не выдаёт токены. Транзакционная advisory-блокировка сериализует seed;
сервисные транзакции выполняются как SAVEPOINT внутри общей транзакции.

Ручные изменения коллекции не откатываются: повтор возвращает её текущие статусы.
Несовпадающий существующий профиль или неполная коллекция вызывают отказ, а не
перезапись чужих данных. Для восстановления исходных четырёх статусов используйте
новую отдельную demo-БД либо описанный в README сброс собственного demo-тома.

## Примеры запросов

В примерах `<AUTHOR_TOKEN>`, `<OTHER_TOKEN>`, `<MODERATOR_TOKEN>` и `<ID>` —
подстановки, не действующие реквизиты. Полностью автоматическая версия выше сама
создаёт нужные личности и использует реальные ID.

Создание (без фото тоже допустимо):

```http
POST /submitData/ HTTP/1.1
Host: localhost:8000
Authorization: Bearer <AUTHOR_TOKEN>
Content-Type: application/json

{"title":"Учебный перевал","user":{"email":"demo.author@example.com","fam":"Примеров","name":"Алексей"},"coords":{"latitude":45.0,"longitude":90.0,"height":2500},"level":{"summer":"1A"},"images":[]}
```

Ответ: HTTP 201, `{"status":201,"message":"Отправлено успешно","id":<ID>}`.

```http
PATCH /submitData/<ID> HTTP/1.1
Authorization: Bearer <AUTHOR_TOKEN>
Content-Type: application/json

{"coords":{"height":2600},"level":{"spring":"1B"}}
```

Ответ: HTTP 200, `{"state":1,"message":"Обновлено"}`. Тот же PATCH с
`<OTHER_TOKEN>` получает HTTP 403:

```json
{"detail":{"code":"FORBIDDEN","message":"Недостаточно прав"}}
```

Модератор выполняет два запроса по очереди:

```http
PATCH /moderation/perevals/<ID>/status HTTP/1.1
Authorization: Bearer <MODERATOR_TOKEN>
Content-Type: application/json

{"expected_status":"new","status":"pending"}
```

```http
PATCH /moderation/perevals/<ID>/status HTTP/1.1
Authorization: Bearer <MODERATOR_TOKEN>
Content-Type: application/json

{"expected_status":"pending","status":"accepted"}
```

Оба ответа — HTTP 200 с `id` и `status`. Альтернативная конечная ветвь:
`{"expected_status":"pending","status":"rejected","reason":"Нужно уточнить маршрут"}`.
Причина отказа обязательна, непустая, до 1000 символов. Для уже принятой заявки
эту ветвь выполнить нельзя: создайте другую и сначала переведите её в `pending`.

```http
GET /submitData/<ID>/history?limit=20&offset=0 HTTP/1.1
Authorization: Bearer <AUTHOR_TOKEN>
```

Ответ — массив решений с полями `id`, `pereval_id`, `old_status`, `new_status`,
`actor: {user_id, role}`, `changed_at` (UTC), `reason`. История новой заявки — `[]`.
Повторный PATCH содержимого принятой заявки получает HTTP 409:

```json
{"detail":{"code":"PEREVAL_NOT_EDITABLE","message":"Редактирование возможно только в статусе new"}}
```

Пустое название `{"title":""}` получает HTTP 422, включая точное место ошибки:

```json
{"detail":{"code":"VALIDATION_ERROR","message":"Некорректные данные запроса","errors":[{"loc":["body","title"],"type":"string_too_short","message":"Строка слишком короткая"}]}}
```

Другие значимые отказы:

| HTTP / code | Причина |
| --- | --- |
| 401 UNAUTHORIZED | Нет действительного Bearer; также заголовок WWW-Authenticate: Bearer |
| 404 PEREVAL_NOT_FOUND | Допустимый ID не существует |
| 409 USER_PROFILE_CONFLICT | Профиль POST не совпал с личностью токена |
| 409 STATUS_CONFLICT | expected_status устарел |
| 409 INVALID_STATUS_TRANSITION | Запрещённый/повторный переход при совпавшем expected_status |
| 413 PAYLOAD_TOO_LARGE | Превышен лимит тела запроса |
| 422 VALIDATION_ERROR | Некорректные поля, типы, фото или причина отказа |
| 500 INTERNAL_ERROR | Непредвиденный сбой; детали БД/секреты не выдаются клиенту |

## Ручной доступ в Swagger

Автоматический сценарий не выводит токены, поэтому для интерактивного Swagger
оператор может отдельно выдать короткий токен после seed. Только в локальном demo:

```powershell
$author = docker compose --env-file .env.demo -p pereval-demo -f docker-compose.yml -f docker-compose.demo.yml exec -T app python -m app.cli issue --email demo.author@example.com --role author --ttl-hours 1 | ConvertFrom-Json
$headers = @{ Authorization = "Bearer $($author.token)" }
Invoke-RestMethod 'http://localhost:8000/submitData/?user__email=demo.author@example.com' -Headers $headers
```

`$author.token` содержит секрет. В Swagger нажмите **Authorize**, вставьте только
значение токена; Swagger добавит Bearer. Не сохраняйте его в документацию, репозиторий
или общедоступную запись экрана. Для модераторских операций выдайте отдельный токен
той же CLI с `--email demo.moderator@example.com --role moderator --ttl-hours 1`.
Это ручная операторская операция, не публичный способ входа.

После ручного показа отзовите каждый выданный токен по его ID (автоматический
`run` отзывает только собственные токены):

```powershell
docker compose --env-file .env.demo -p pereval-demo -f docker-compose.yml -f docker-compose.demo.yml exec -T app python -m app.cli revoke --token-id $author.token_id
```
