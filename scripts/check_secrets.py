"""Scan all Git history and files destined for Git, without reading local .env.

Requires Docker, not a paid Gitleaks action/license. Current files and every
reachable commit are checked without per-finding or inline exemptions.
"""

import json
from pathlib import Path
import secrets
import shutil
import string
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "zricethezav/gitleaks:v8.30.0@sha256:691af3c7c5a48b16f187ce3446d5f194838f91238f27270ed36eef6359a574d9"


def scan(source, command, report, *extra):
    result = subprocess.run([
        "docker", "run", "--rm",
        "--mount", f"type=bind,source={source},target=/scan,readonly",
        "--mount", f"type=bind,source={ROOT / '.gitleaks.toml'},target=/config.toml,readonly",
        "--mount", f"type=bind,source={report.parent},target=/reports",
        IMAGE, command, "--config", "/config.toml", "--gitleaks-ignore-path", "/dev/null",
        "--ignore-gitleaks-allow",
        "--redact", "--no-banner", "--report-format", "json",
        "--report-path", f"/reports/{report.name}", *extra, "/scan",
    ], cwd=ROOT, timeout=180)
    if result.returncode not in (0, 1) or not report.is_file():
        raise RuntimeError("Gitleaks failed to produce a report")
    return result.returncode, json.loads(report.read_text(encoding="utf-8"))


def main():
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fstr-secret-scan-") as directory:
        temporary = Path(directory)
        canary = temporary / "canary"
        canary.mkdir()
        # Construct disposable synthetic secrets at runtime. Verify both the
        # standard detector and our low-entropy database password rule work.
        token = "ghp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36))
        (canary / "probe.txt").write_text(f"FSTR_DB_PASS=qa-canary\ngithub_token={token}\n", encoding="utf-8")
        print("Gitleaks canary: two synthetic findings are expected", flush=True)
        code, findings = scan(canary, "dir", temporary / "canary.json")
        if code != 1 or not {"fstr-database-password", "github-pat"} <= {row["RuleID"] for row in findings}:
            raise RuntimeError("Gitleaks canary failed: standard and database secret rules must detect")

        snapshot = temporary / "checkout"
        snapshot.mkdir()
        names = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
        ).decode("utf-8").split("\0")
        for name in set(filter(None, names)):
            source = ROOT / name
            if not source.is_file():
                continue
            filename = Path(name).name
            if filename == ".env" or (filename.startswith(".env.") and filename != ".env.example"):
                raise RuntimeError("Refusing an environment secret file destined for Git")
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

        current_code, _ = scan(snapshot, "dir", reports / "gitleaks-current.json")
        history_code, _ = scan(ROOT, "git", reports / "gitleaks-history.json", "--log-opts=--all")
    if current_code or history_code:
        raise SystemExit("Secrets detected; inspect redacted reports/gitleaks-*.json")
    print("Gitleaks: current Git files and complete history passed without finding exemptions", flush=True)


if __name__ == "__main__":
    main()
