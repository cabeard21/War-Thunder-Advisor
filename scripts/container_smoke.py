"""Exercise the ARM64 image with disposable bind-mounted data before publication."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4


def docker(*arguments: str) -> str:
    return subprocess.check_output(["docker", *arguments], text=True).strip()


def wait_for_health(name: str) -> None:
    for _ in range(90):
        state = docker("inspect", "--format", "{{.State.Health.Status}}", name)
        if state == "healthy":
            return
        if docker("inspect", "--format", "{{.State.Running}}", name) != "true":
            break
        time.sleep(2)
    raise RuntimeError(f"Container failed health check:\n{docker('logs', name)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    name = f"wt-advisor-smoke-{uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="wt-advisor-image-") as temporary:
        data = Path(temporary).resolve() / "data"
        data.mkdir(mode=0o755)
        mount = f"type=bind,source={data},target=/data"
        # Prepare permissions with the image itself; no host sudo or broad chmod.
        docker("run", "--rm", "--platform", "linux/arm64", "--user", "0:0", "--mount",
               mount, "--entrypoint", "chown", args.image, "10001:10001", "/data")
        prepare = """
from alembic import command
from wt_advisor.storage.db import build_engine, _alembic_config
from sqlalchemy import text
engine = build_engine('/data/wt-advisor.sqlite')
command.upgrade(_alembic_config(engine), '0002')
with engine.begin() as connection:
    connection.execute(text('CREATE TABLE smoke_marker (value TEXT NOT NULL)'))
    connection.execute(text("INSERT INTO smoke_marker VALUES ('persistent')"))
engine.dispose()
"""
        try:
            docker("run", "--rm", "--platform", "linux/arm64", "--mount", mount,
                   "--entrypoint", "python", args.image, "-c", prepare)
            docker("run", "--detach", "--name", name, "--platform", "linux/arm64",
                   "--mount", mount, "--env", "WT_ADVISOR_ALLOWED_HOSTS=pi.local", args.image)
            wait_for_health(name)
            check = """
import json, re, sqlite3, urllib.request
from alembic.script import ScriptDirectory
from alembic.config import Config
health = json.load(urllib.request.urlopen('http://127.0.0.1:8765/health'))
assert health == {'status': 'ok'}, health
page = urllib.request.urlopen('http://127.0.0.1:8765/').read().decode()
assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page)
assert assets, 'Bundled dashboard assets missing'
for asset in assets:
    assert urllib.request.urlopen('http://127.0.0.1:8765' + asset).status == 200
with sqlite3.connect('/data/wt-advisor.sqlite') as connection:
    assert connection.execute('SELECT value FROM smoke_marker').fetchone() == ('persistent',)
    version = connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]
    assert version == ScriptDirectory.from_config(Config('/app/alembic.ini')).get_current_head()
"""
            docker("exec", name, "python", "-c", check)
            docker("restart", name)
            wait_for_health(name)
            docker("exec", name, "python", "-c", check)
            assert (data / "wt-advisor.sqlite").is_file()
            print("ARM64 health, bundled assets, migration, and restart persistence passed")
        finally:
            subprocess.run(["docker", "rm", "--force", name], check=False)
            # The private temporary directory contains only this smoke test's database.
            docker("run", "--rm", "--platform", "linux/arm64", "--user", "0:0",
                   "--mount", mount, "--entrypoint", "python", args.image, "-c",
                   "from pathlib import Path; [p.unlink() for p in Path('/data').iterdir()]")


if __name__ == "__main__":
    main()
