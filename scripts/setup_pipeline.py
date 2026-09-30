"""Create local-only Postgres credentials without overwriting existing configuration."""

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / "infra/postgres/.env"
    if path.exists():
        print("infra/postgres/.env already exists; left unchanged.")
        return
    admin = secrets.token_urlsafe(24)
    reader = secrets.token_urlsafe(24)
    with path.open("x") as output:
        path.chmod(0o600)
        output.write(
            f"POSTGRES_PASSWORD={admin}\nPIPELINE_READER_PASSWORD={reader}\n"
            f"PIPELINE_DATABASE_URL=postgresql://pipeline_reader:{reader}@127.0.0.1:5433/pipeline_demo\n"
        )
    print("Created infra/postgres/.env with local credentials (ignored by Git).")


if __name__ == "__main__":
    main()
