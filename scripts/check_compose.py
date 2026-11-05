"""Verify Compose on a new project/volume; never use the user's .env or DB.

Run from a clean checkout: python scripts/check_compose.py
Only this script's containers/network are stopped afterward; its test volume stays.
CI uses --remove-volume to remove only the volume this invocation created.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove-volume", action="store_true", help="Remove this run's new disposable volume")
    args = parser.parse_args()
    project = "fstr-qa-" + secrets.token_hex(5)
    volume = project + "_postgres18_data"
    password = secrets.token_urlsafe(24) + "@:/#?%$"
    issued_secrets = []
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FSTR_", "COMPOSE_")) and k != "PORT"}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    def redact(output):
        output = output.replace(password, "[REDACTED]")
        for token in issued_secrets:
            output = output.replace(token, "[REDACTED TOKEN]")
        return re.sub(r"postgres(?:ql)?(?:\+\w+)?://\S+", "[REDACTED DSN]", output)

    def run(args, *, check=True):
        result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if check and result.returncode:
            lines = redact(result.stdout + result.stderr).splitlines()
            relevant = [line for line in lines if re.search(r"error|fatal|failed|not found", line, re.I)]
            raise RuntimeError("\n".join([*relevant[-40:], *lines[-25:]]))
        return result

    if run(["docker", "volume", "inspect", volume], check=False).returncode == 0:
        raise RuntimeError("Refusing to reuse an existing volume")

    with tempfile.TemporaryDirectory(prefix="fstr-compose-") as directory:
        env_file = Path(directory) / "compose.env"
        env_file.write_text(
            "FSTR_DB_NAME=fstr_smoke\nFSTR_DB_LOGIN=smoke_test\n"
            f"FSTR_DB_PASS='{password}'\nPORT={port}\n", encoding="utf-8",
        )
        prefix = ["docker", "compose", "--project-name", project, "--env-file", str(env_file), "-f", str(ROOT / "docker-compose.yml")]

        def compose(*args):
            return run([*prefix, *args])

        authorization = {}

        def request(path, data=None, *, method=None, headers=None, expected=None):
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                         method=method,
                                         headers={"Content-Type": "application/json", **(authorization if headers is None else headers)})
            try:
                response = urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                result = response.status, response.read()
                if expected is not None:
                    assert response.status == expected, f"{req.method} {path}: HTTP {response.status}, expected {expected}"
                return result

        def error(path, status, code, **kwargs):
            _, body = request(path, expected=status, **kwargs)
            assert json.loads(body)["detail"]["code"] == code

        def wait_for(path):
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    return request(path)
                except (OSError, urllib.error.URLError):
                    time.sleep(1)
            raise RuntimeError("API did not recover within 60 seconds")

        print(f"New isolated project: {project}; volume: {volume}", flush=True)
        try:
            compose("config", "--quiet")
            print("Building runtime image and starting PostgreSQL -> migration -> API...", flush=True)
            compose("up", "--build", "-d")
            assert wait_for("/health") == (200, b'{"status":"ok"}')
            assert request("/docs")[0] == 200
            compose("run", "--rm", "migrate")
            compose("run", "--rm", "migrate", "python", "-m", "alembic", "check")

            container = compose("ps", "-q", "postgres").stdout.strip()
            info = json.loads(run(["docker", "inspect", container]).stdout)[0]
            assert any(m["Name"] == volume and m["Destination"] == "/var/lib/postgresql" for m in info["Mounts"] if m["Type"] == "volume")
            pgdata = compose("exec", "-T", "postgres", "sh", "-c", 'printf "%s" "$PGDATA"').stdout.strip()
            assert pgdata == "/var/lib/postgresql/18/docker"
            compose("exec", "-T", "app", "python", "-c", "import os,sys; assert os.geteuid() != 0; assert sys.version_info[:3] == (3,13,9)")
            print("Migration, repeat upgrade, model/schema comparison, health/docs and PG18 mount: OK", flush=True)

            def issue(email, role):
                issued = json.loads(compose("exec", "-T", "app", "python", "-m", "app.cli", "issue",
                                            "--email", email, "--role", role,
                                            "--fam", "Test", "--name", "Test").stdout)
                issued_secrets.append(issued["token"])
                return {"Authorization": "Bearer " + issued["token"]}

            author = issue("smoke-author@example.com", "author")
            other = issue("smoke-other@example.com", "author")
            moderator = issue("smoke-mod@example.com", "moderator")
            authorization.update(author)
            payload = {
                "title": "Container HTTP smoke",
                "beauty_title": "пер.",
                "user": {"email": "smoke-author@example.com", "fam": "Test", "name": "Test"},
                "coords": {"latitude": -90, "longitude": 180, "height": 1},
                "level": {"spring": "1A"},
            }
            _, body = request("/submitData/", payload, expected=201)
            created = json.loads(body)
            assert created["status"] == 201
            item_id = created["id"]
            path = f"/submitData/{item_id}"
            error(path, 401, "UNAUTHORIZED", headers={})
            error(path, 403, "FORBIDDEN", headers=other)
            error(path, 403, "FORBIDDEN", headers=moderator, method="PATCH", data={"title": "Forbidden"})
            error("/moderation/perevals", 403, "FORBIDDEN")
            error("/submitData/2147483647", 404, "PEREVAL_NOT_FOUND")
            before = json.loads(request(path, expected=200)[1])
            error(path, 422, "VALIDATION_ERROR", method="PATCH", data={"title": "Partial leak", "coords": {"height": 10001}})
            assert json.loads(request(path, expected=200)[1]) == before
            request(path, method="PATCH", expected=200, data={
                "title": "Edited in container", "beauty_title": None,
                "coords": {"height": 10000}, "level": {"spring": None, "winter": "2Б"},
            })
            edited = json.loads(request(path, expected=200)[1])
            assert edited["coords"] == {"latitude": -90, "longitude": 180, "height": 10000}
            assert edited["beauty_title"] is None and edited["level"]["spring"] is None
            assert edited["level"]["winter"] == "2B"
            assert len(json.loads(request("/submitData/?user__email=smoke-author@example.com", expected=200)[1])) == 1

            for final in ["accepted", "rejected"]:
                current_id = item_id if final == "accepted" else json.loads(request("/submitData/", payload, expected=201)[1])["id"]
                route = f"/moderation/perevals/{current_id}/status"
                for old, new in [("new", "pending"), ("pending", final)]:
                    data = {"expected_status": old, "status": new, "reason": "Container review"}
                    request(route, data, method="PATCH", headers=moderator, expected=200)
                    error(route, 409, "STATUS_CONFLICT", data=data, method="PATCH", headers=moderator)
                error(f"/submitData/{current_id}", 409, "PEREVAL_NOT_EDITABLE", method="PATCH", data={"title": "Too late"})
                history = json.loads(request(f"/submitData/{current_id}/history", expected=200)[1])
                assert [(row["old_status"], row["new_status"]) for row in history] == [("new", "pending"), ("pending", final)]
                assert all(row["actor"]["role"] == "moderator" and row["changed_at"].endswith("Z") for row in history)
            print("TCP HTTP: author/other/moderator, 401/403/404/409/422, partial/null, both moderation branches: OK", flush=True)

            compose("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60", "postgres")
            status, body = wait_for(f"/submitData/{item_id}")
            assert status == 200 and json.loads(body) == {**edited, "status": "accepted"}
            compose("run", "--rm", "migrate")

            logs = compose("logs", "--no-color").stdout
            assert password not in logs
            assert all(token not in logs for token in issued_secrets)
            assert re.search(r"postgres(?:ql)?(?:\+\w+)?://", logs) is None
            files = compose("exec", "-T", "app", "python", "-c", "from pathlib import Path; import json; print(json.dumps(sorted(str(p) for p in Path('/app').rglob('*') if p.is_file())))").stdout
            assert not any(part in files for part in ["/.env", "/.git/", "/.idea/", "get-docker.sh", "install.ps1", "init.sql", "__pycache__"])
            compose("exec", "-T", "app", "python", "-c", "from importlib.util import find_spec; assert all(find_spec(name) is None for name in ['pytest', 'httpx', 'httpx2', 'piptools'])")
            print("Persistence after PostgreSQL container recreation, safe logs and runtime files: OK", flush=True)
        finally:
            compose("down", *(["--volumes"] if args.remove_volume else []))
            if args.remove_volume:
                assert run(["docker", "volume", "inspect", volume], check=False).returncode != 0
                print(f"Removed only {project} and its new test volume", flush=True)
            else:
                print(f"Stopped only {project}. Its new test volume is preserved: {volume}", flush=True)


if __name__ == "__main__":
    main()
