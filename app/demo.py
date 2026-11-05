"""Explicit local demo: python -m app.demo seed|run [--pause]. No public tokens."""

import argparse
import base64
from io import BytesIO
import json
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from PIL import Image as PillowImage
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import services
from app.cli import issue_token, revoke_token
from app.config import get_settings
from app.database import build_engine
from app.models import PerevalAdded, User
from app.schemas import PerevalCreate, StatusUpdate, UserCreate
from app.security import Principal, Role


PROFILES = {
    "seed": {"email": "demo.collection@example.com", "fam": "Примеров", "name": "Архив"},
    "author": {"email": "demo.author@example.com", "fam": "Примеров", "name": "Алексей"},
    "stranger": {"email": "demo.other@example.com", "fam": "Тестова", "name": "Мария"},
    "moderator": {"email": "demo.moderator@example.com", "fam": "Учебная", "name": "Модерация"},
}


def require_demo(settings):
    if not settings.demo_mode or not settings.db_name.endswith("_demo"):
        raise ValueError("Demo requires FSTR_DEMO_MODE=true and FSTR_DB_NAME ending in _demo")


def payload(profile, title="Учебный перевал — показ"):
    # Original synthetic fixture, not a photograph or evidence of a real route.
    buffer = BytesIO()
    PillowImage.new("RGB", (16, 16), (70, 130, 180)).save(buffer, format="PNG")
    return {
        "title": title, "user": profile,
        "coords": {"latitude": 45.0, "longitude": 90.0, "height": 2500},
        "level": {"summer": "1A"},
        "images": [{"title": "Синтетический тестовый PNG 16x16",
                    "img": base64.b64encode(buffer.getvalue()).decode("ascii")}],
    }


def seed(engine):
    """All-or-nothing dataset, serialized across concurrent seed processes.

    The collection has its own owner; replay submissions use another identity.
    Existing collection rows are never reset or duplicated, even after edits.
    Session commits below release savepoints; only the outer transaction commits.
    """
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(614006)"))
        principals = {}
        with Session(connection, join_transaction_mode="create_savepoint") as db, db.begin():
            collection_exists = False
            for key, profile in PROFILES.items():
                user = db.scalar(select(User).where(User.email == profile["email"]))
                if key == "seed":
                    collection_exists = user is not None
                if user is None:
                    user = User(**UserCreate.model_validate(profile).model_dump())
                    db.add(user)
                    db.flush()
                elif any(getattr(user, field) != value
                         for field, value in UserCreate.model_validate(profile).model_dump().items()):
                    raise ValueError("Demo identity conflicts with an existing profile")
                principals[key] = Principal(user.id, user.email,
                                            Role.MODERATOR if key == "moderator" else Role.AUTHOR)
            rows = list(db.scalars(select(PerevalAdded).where(
                PerevalAdded.user_id == principals["seed"].user_id,
            ).order_by(PerevalAdded.id)))
            if collection_exists:
                if len(rows) != 4:
                    raise ValueError("Existing demo collection is incomplete; use a fresh demo database")
                return [{"id": row.id, "status": row.status} for row in rows]

        result = []
        for status in ("new", "pending", "accepted", "rejected"):
            with Session(connection, join_transaction_mode="create_savepoint") as db:
                item_id = services.create_pereval(db, PerevalCreate.model_validate(
                    payload(PROFILES["seed"], f"Учебная коллекция — {status}")), principals["seed"])
                if status != "new":
                    services.change_status(db, item_id, StatusUpdate(expected_status="new", status="pending"),
                                           principals["moderator"])
                if status in {"accepted", "rejected"}:
                    services.change_status(db, item_id, StatusUpdate(
                        expected_status="pending", status=status,
                        reason="Учебный отказ: требуется уточнение маршрута" if status == "rejected" else None,
                    ), principals["moderator"])
            result.append({"id": item_id, "status": status})
        return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("Demo HTTP target must be a loopback http://host:port origin")
    return value.rstrip("/")


def run(engine, base_url, pause=False):
    base_url = local_url(base_url)  # Validate before issuing any credentials.
    seed(engine)
    tokens = {}
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def step(label):
        print(f"\n{label}", flush=True)
        if pause:
            input("Enter — выполнить шаг: ")

    def request(method, path, actor=None, body=None, expected=200, code=None):
        headers = {"Content-Type": "application/json"}
        if actor:
            headers["Authorization"] = "Bearer " + tokens[actor]["token"]
        req = Request(base_url + path, method=method, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
        try:
            response = opener.open(req, timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            status, data = response.code, json.load(response)
        print(f"{method} {path} [{actor or 'anonymous'}] -> {status}", flush=True)
        print(json.dumps(data, ensure_ascii=False), flush=True)
        if status != expected or (code and data.get("detail", {}).get("code") != code):
            raise RuntimeError("Unexpected HTTP result")
        return data

    try:
        for actor in ("author", "stranger", "moderator"):
            with Session(engine) as db:
                tokens[actor] = issue_token(db, **PROFILES[actor], ttl_hours=1,
                                           role=Role.MODERATOR if actor == "moderator" else Role.AUTHOR)
        step("1. Создание заявки с синтетическим PNG")
        created = request("POST", "/submitData/", "author", payload(PROFILES["author"]), expected=201)
        path = f"/submitData/{created['id']}"
        step("2. Частичный PATCH: только высота и весенний сезон")
        request("PATCH", path, "author", {"coords": {"height": 2600}, "level": {"spring": "1B"}})
        detail = request("GET", path, "author")
        if (detail["coords"] != {"latitude": 45.0, "longitude": 90.0, "height": 2600}
                or detail["level"]["spring"] != "1B" or detail["level"]["summer"] != "1A"
                or detail["images"] != payload(PROFILES["author"])["images"]):
            raise RuntimeError("Partial update lost data")
        step("3. Чужому автору отказано; ошибка валидации не меняет заявку")
        request("PATCH", path, "stranger", {"title": "Чужая правка"}, expected=403, code="FORBIDDEN")
        request("PATCH", path, "author", {"title": ""}, expected=422, code="VALIDATION_ERROR")
        step("4. Модератор: new -> pending -> accepted")
        decision_path = f"/moderation/perevals/{created['id']}/status"
        request("PATCH", decision_path, "moderator", {"expected_status": "new", "status": "pending"})
        request("PATCH", decision_path, "moderator", {"expected_status": "pending", "status": "accepted"})
        step("5. История: два решения с ID модератора и временем")
        history = request("GET", path + "/history", "author")
        if [(row["old_status"], row["new_status"]) for row in history] != [
            ("new", "pending"), ("pending", "accepted"),
        ] or any(row["actor"]["user_id"] != tokens["moderator"]["user_id"] for row in history):
            raise RuntimeError("Unexpected decision history")
        step("6. После решения редактирование запрещено")
        request("PATCH", path, "author", {"title": "Поздняя правка"},
                expected=409, code="PEREVAL_NOT_EDITABLE")
        final = request("GET", path, "author")
        if final != {**detail, "status": "accepted"}:
            raise RuntimeError("Rejected requests changed the submission")
        request("GET", path, expected=401, code="UNAUTHORIZED")
    finally:
        failed = []
        for token in tokens.values():
            try:
                with Session(engine) as db:
                    if not revoke_token(db, token["token_id"]):
                        failed.append(token["token_id"])
            except Exception:
                failed.append(token["token_id"])
        if failed:
            print(f"Could not revoke token IDs {failed}; revoke via app.cli; TTL is 1 hour", file=sys.stderr)
            raise RuntimeError("Demo token cleanup failed")
        print("Временные токены отозваны; секреты не выводились.", flush=True)
    print("DEMO OK: 201 -> 200 -> 403 -> moderation -> history -> 409", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="Идемпотентная коллекция четырёх статусов; без выдачи токенов")
    replay = sub.add_parser("run", help="Сетевой сценарий, новые заявки при каждом повторе")
    replay.add_argument("--base-url", default="http://127.0.0.1:8000")
    replay.add_argument("--pause", action="store_true", help="Пауза перед каждым шагом для показа")
    args = parser.parse_args(argv)
    engine = None
    try:
        settings = get_settings()
        require_demo(settings)
        engine = build_engine(settings)
        if args.command == "seed":
            print(json.dumps({"collection": seed(engine), "tokens_issued": 0}))
        else:
            run(engine, args.base_url, args.pause)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("Demo interrupted", file=sys.stderr)
        return 1
    except Exception:
        print("Demo failed: check explicit demo mode, *_demo database, migrations and local API. "
              "Existing profiles/collection must not conflict. No secrets printed.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
