"""Reapply the demo seed and reader permissions without deleting the database volume."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "--env-file", ".env", "-p", "p045-b", "-f", "infra/postgres/compose.yml"]


def main():
    for filename, command in [
        ("001_seed.sql", ["psql", "-U", "pipeline_admin", "-d", "pipeline_demo", "-v", "ON_ERROR_STOP=1"]),
        ("002_reader.sh", ["sh", "-s"]),
    ]:
        # Gửi bytes, không dùng text=True: trên Windows chế độ text đổi "\n" thành "\r\n" và `sh` trong container
        # báo lỗi cú pháp ở 002_reader.sh.
        subprocess.run(
            [*COMPOSE, "exec", "-T", "postgres", *command],
            cwd=ROOT,
            input=(ROOT / "infra/postgres/init" / filename).read_bytes(),
            check=True,
        )


if __name__ == "__main__":
    main()
