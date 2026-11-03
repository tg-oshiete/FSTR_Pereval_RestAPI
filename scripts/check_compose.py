"""Verify Compose on a new project/volume; never use the user's .env or DB.

Run from a clean checkout: python scripts/check_compose.py
Only this script's containers/network are stopped afterward; its test volume stays.
"""

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
    project = "fstr-compose-" + secrets.token_hex(5)
    volume = project + "_postgres18_data"
    password = secrets.token_urlsafe(24) + "@:/#?%$"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("FSTR_", "COMPOSE_")) and k != "PORT"}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    def redact(output):
        output = output.replace(password, "[REDACTED]")
        return re.sub(r"postgres(?:ql)?(?:\+\w+)?://\S+", "[REDACTED DSN]", output)

    def run(args, *, check=True):
        result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
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

        def request(path, data=None):
            body = json.dumps(data).encode() if data is not None else None
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()

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
            print("Migration, repeat upgrade, model/schema comparison, health/docs and PG18 mount: OK", flush=True)

            status, body = request("/submitData/", {
                "title": "Persistence test",
                "user": {"email": "smoke@example.com", "fam": "Test", "name": "Test"},
                "coords": {"latitude": 20, "longitude": 80, "height": 3200},
                "level": {},
            })
            assert status == 201
            item_id = json.loads(body)["id"]
            compose("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "60", "postgres")
            status, body = wait_for(f"/submitData/{item_id}")
            assert status == 200 and json.loads(body)["title"] == "Persistence test"
            compose("run", "--rm", "migrate")

            logs = compose("logs", "--no-color").stdout
            assert password not in logs
            assert re.search(r"postgres(?:ql)?(?:\+\w+)?://", logs) is None
            files = compose("exec", "-T", "app", "python", "-c", "from pathlib import Path; import json; print(json.dumps(sorted(str(p) for p in Path('/app').rglob('*') if p.is_file())))").stdout
            assert not any(part in files for part in ["/.env", "/.git/", "/.idea/", "get-docker.sh", "install.ps1", "init.sql", "__pycache__"])
            compose("exec", "-T", "app", "python", "-c", "from importlib.util import find_spec; assert all(find_spec(name) is None for name in ['pytest', 'httpx', 'piptools'])")
            print("Persistence after PostgreSQL container recreation, safe logs and runtime files: OK", flush=True)
        finally:
            compose("down")
            print(f"Stopped only {project}. Its new test volume is preserved: {volume}", flush=True)


if __name__ == "__main__":
    main()
