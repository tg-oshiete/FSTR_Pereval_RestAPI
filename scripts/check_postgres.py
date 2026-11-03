"""Storage smoke test in a NEW disposable PostgreSQL 18 container.

No user .env, existing database, volume or container is used. The container has
tmpfs storage and is removed on exit. Run: python scripts/check_postgres.py
"""

from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base, build_engine, get_db
from app.main import create_app
from app.models import Image, PerevalAdded, PerevalImages
from app.validation import DEFAULT_LIMITS


PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgaPgPAAIDAYAkYfWXAAAAAElFTkSuQmCC"


def check(engine, settings):
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
        # Synthetic legacy shared ownership must survive the widening migration.
        connection.execute(text("INSERT INTO users (id,email,fam,name) VALUES (1,'legacy@example.com','Test','Test')"))
        connection.execute(text("INSERT INTO coords (id,latitude,longitude,height) VALUES (1,20,80,3200),(2,20,80,3200)"))
        connection.execute(text("INSERT INTO pereval_added (id,title,user_id,coord_id,level_spring,add_time) VALUES (1,'Legacy',1,1,'1A','2026-10-31T12:00:00Z'),(2,'Legacy',1,2,'2B','2026-10-31T12:00:00Z')"))
        connection.execute(text("INSERT INTO images (id,img,title) VALUES (1,decode(:img,'base64'),'Legacy')"), {"img": PNG})
        connection.execute(text("INSERT INTO pereval_images (id_pereval,id_image) VALUES (1,1),(2,1)"))
        # Synthetic explicit IDs must leave sequences ready for the integrity migration's copies.
        for table in ("users", "coords", "pereval_added", "images"):
            connection.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}','id'), (SELECT max(id) FROM {table}))"))
        command.upgrade(config, "head")
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.scalar(text("SELECT level_spring FROM pereval_added WHERE id=1")) == "1A"
        assert connection.scalar(text("SELECT count(DISTINCT id_image) FROM pereval_images")) == 2
        # Reset sequences after explicit synthetic IDs.
        for table in ("users", "coords", "pereval_added", "images"):
            connection.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}','id'), (SELECT max(id) FROM {table}))"))

    app = create_app(settings)

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        assert client.patch("/submitData/1", json={"images": []}).status_code == 200
        assert len(client.get("/submitData/2").json()["images"]) == 1
        with Session(engine) as fresh:
            # The integrity migration splits legacy shared ownership before enforcing uniqueness.
            assert fresh.get(Image, 1) is None
            assert fresh.query(PerevalImages).filter_by(id_pereval=2).count() == 1

        payload = {"title": "Storage smoke", "user": {"email": "author@example.com", "fam": "Test", "name": "Test"},
                   "coords": {"latitude": 20, "longitude": 80, "height": 3200},
                   "level": {"spring": "н/к"}, "add_time": "2026-10-31T15:00:00+03:00",
                   "images": [{"title": "Photo", "img": PNG}]}
        response = client.post("/submitData/", json=payload)
        assert response.status_code == response.json()["status"] == 201, "POST with n/k failed"
        item_id = response.json()["id"]
        for season in ("spring", "summer", "autumn", "winter"):
            assert client.patch(f"/submitData/{item_id}", json={"level": {season: "н/к"}}).status_code == 200
            with Session(engine) as fresh:
                assert getattr(fresh.get(PerevalAdded, item_id), f"level_{season}") == "n/k"
            assert client.get(f"/submitData/{item_id}").json()["level"][season] == "n/k"
        assert client.patch(f"/submitData/{item_id}", json={"coords": {"height": 3300}, "level": {"winter": None}}).status_code == 200
        result = client.get(f"/submitData/{item_id}").json()
        assert result["coords"] == {"latitude": 20, "longitude": 80, "height": 3300}
        assert result["add_time"] == "2026-10-31T12:00:00Z" and result["date_added"].endswith("Z")
        assert result["level"]["winter"] is None
        assert client.patch(f"/submitData/{item_id}", json={"images": [{"title": "New", "img": PNG}]}).status_code == 200
        with Session(engine) as fresh:
            assert fresh.query(Image).count() == 2  # Legacy shared photo + the replacement.
            assert fresh.query(PerevalImages).filter_by(id_pereval=item_id).count() == 1
        before = client.get(f"/submitData/{item_id}").json()
        invalid = client.patch(f"/submitData/{item_id}", json={"title": "Changed", "images": [{"title": "Bad", "img": "a"}]})
        assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "VALIDATION_ERROR"
        assert client.get(f"/submitData/{item_id}").json() == before
        for status in ("pending", "accepted", "rejected"):
            with Session(engine) as session:
                session.get(PerevalAdded, item_id).status = status
                session.commit()
            response = client.patch(f"/submitData/{item_id}", json={"title": "Forbidden"})
            assert response.status_code == 409 and response.json()["detail"]["code"] == "PEREVAL_NOT_EDITABLE"
            with Session(engine) as fresh:
                assert fresh.get(PerevalAdded, item_id).title == "Storage smoke"
    print("PG18: migration of legacy shared photos, n/k, four seasons, UTC, partial/null, image replacement and 409/422: OK", flush=True)


def main():
    name = "fstr-storage-" + secrets.token_hex(6)
    password = secrets.token_urlsafe(32)
    env = {key: value for key, value in os.environ.items() if not key.startswith(("FSTR_", "POSTGRES_"))}
    env.update(POSTGRES_PASSWORD=password, POSTGRES_USER="storage_test", POSTGRES_DB="storage_test")

    def docker(*args, check=True):
        result = subprocess.run(["docker", *args], env=env, capture_output=True, text=True, timeout=90)
        if check and result.returncode:
            raise RuntimeError("Isolated Docker operation failed")
        return result

    assert docker("container", "inspect", name, check=False).returncode != 0, "Refusing to reuse a container"
    container = None
    engine = None
    try:
        container = docker("run", "--detach", "--rm", "--name", name, "--tmpfs", "/var/lib/postgresql",
                           "--publish", "127.0.0.1::5432", "--env", "POSTGRES_PASSWORD", "--env", "POSTGRES_USER",
                           "--env", "POSTGRES_DB", "postgres:18").stdout.strip()
        print(f"New disposable PG18: {name}", flush=True)
        deadline = time.monotonic() + 60
        # initdb starts a temporary Unix-socket-only server. Wait for final TCP
        # readiness so the host cannot race that temporary server's shutdown.
        while docker("exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "storage_test", "-d", "storage_test", check=False).returncode:
            if time.monotonic() >= deadline:
                raise RuntimeError("Isolated PostgreSQL did not become ready")
            time.sleep(0.5)
        info = json.loads(docker("inspect", container).stdout)[0]
        port = int(info["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"])
        settings = Settings(_env_file=None, db_host="127.0.0.1", db_port=port, db_name="storage_test",
                            db_login="storage_test", db_pass=password, PORT=8000, **asdict(DEFAULT_LIMITS))
        engine = build_engine(settings)
        check(engine, settings)
    except Exception as exc:
        # Never print connection strings or exception values from the DB driver.
        print(f"PG18 check failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
        if container:
            docker("rm", "--force", container)
            print(f"Removed only {name} and its temporary storage", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
