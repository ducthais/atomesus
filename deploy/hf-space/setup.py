"""Cài combo miễn phí Vercel + Hugging Face Space + Supabase, chạy MỘT lần trên máy bạn (docs/deploy-free.md):

    pip install huggingface_hub
    python deploy/hf-space/setup.py

Việc làm:
1. Nạp dữ liệu nguồn demo (sales_raw, Pagila) vào Supabase và tạo tài khoản chỉ đọc `pipeline_reader`.
   Dùng psql trong Docker (cần Docker Desktop đang chạy). Chạy lại an toàn: Pagila đã có thì bỏ qua.
2. Tạo Space (Docker, công khai) và dataset riêng tư giữ data/, đặt secrets của Space với mật khẩu ngẫu nhiên.
3. In ra giá trị cần dán vào GitHub secrets và Vercel, cùng mật khẩu đăng nhập.

Chạy lại sẽ sinh mật khẩu mới (reader, Engineer, Reviewer, Dagster) và ghi đè secrets của Space.
"""

import getpass
import secrets
import string
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parents[2]
INIT = ROOT / "infra" / "postgres" / "init"
PSQL_IMAGE = "postgres:17"  # psql >= 16.10 mới hiểu lệnh \restrict trong bản dump Pagila

READER_SQL = """
SELECT 'CREATE ROLE pipeline_reader LOGIN' WHERE NOT EXISTS
    (SELECT FROM pg_roles WHERE rolname = 'pipeline_reader') \\gexec
ALTER ROLE pipeline_reader WITH LOGIN PASSWORD :'reader_password';
ALTER ROLE pipeline_reader SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA sales_raw, pagila TO pipeline_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA sales_raw, pagila TO pipeline_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA sales_raw GRANT SELECT ON TABLES TO pipeline_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA pagila GRANT SELECT ON TABLES TO pipeline_reader;
"""


def password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    label = f"{prompt} [{default}]: " if default else f"{prompt}: "
    value = (getpass.getpass(label) if secret else input(label)).strip()
    if not value and not default:
        raise SystemExit(f"Cần nhập: {prompt}")
    return value or default


def supabase_url() -> tuple[str, str, str]:
    """URL admin (session pooler), host và project ref. Session pooler đi qua IPv4; kết nối trực tiếp chỉ có IPv6."""
    url = ask("Supabase → Connect → Session pooler, dán connection string")
    if "[YOUR-PASSWORD]" in url:
        url = url.replace("[YOUR-PASSWORD]", quote(ask("Mật khẩu database Supabase", secret=True), safe=""))
    parts = urlsplit(url)
    user = parts.username or ""
    if "pooler.supabase.com" not in (parts.hostname or "") or "." not in user:
        raise SystemExit("Cần chuỗi kết nối Session pooler (host *.pooler.supabase.com, user postgres.<ref>).")
    return url, f"{parts.hostname}:{parts.port or 5432}", user.split(".", 1)[1]


def psql(url: str, stdin: bytes, *args: str) -> str:
    command = ["docker", "run", "--rm", "-i", PSQL_IMAGE, "psql", url, "-v", "ON_ERROR_STOP=1", "-q", *args]
    result = subprocess.run(command, input=stdin, capture_output=True)
    if result.returncode:
        raise SystemExit(f"psql lỗi:\n{result.stderr.decode(errors='replace')}")
    return result.stdout.decode().strip()


def seed(admin_url: str, reader_password: str) -> None:
    print("== Nạp dữ liệu nguồn vào Supabase ==")
    psql(admin_url, (INIT / "001_seed.sql").read_bytes())
    if psql(admin_url, b"", "-tAc", "SELECT count(*) FROM pg_namespace WHERE nspname = 'pagila'") == "0":
        import gzip

        # Một transaction: lỗi giữa chừng thì không để lại schema dở dang (lần chạy sau sẽ bỏ qua Pagila).
        psql(admin_url, gzip.decompress((INIT / "003_pagila.sql.gz").read_bytes()), "--single-transaction")
    psql(admin_url, READER_SQL.encode(), "-v", f"reader_password={reader_password}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # console Windows mặc định cp1252
    print("Cần: token Hugging Face quyền Write (huggingface.co/settings/tokens), OpenAI API key,")
    print("project Supabase (supabase.com), Docker Desktop đang chạy.\n")
    token = ask("Hugging Face token (Write)", secret=True)
    api = HfApi(token=token)
    owner = api.whoami()["name"]
    space = ask("Tên Space", f"{owner}/atomesus")
    data_repo = ask("Dataset riêng tư giữ data/", f"{owner}/atomesus-data")
    openai_key = ask("OpenAI API key (sk-...)", secret=True)
    admin_url, host, ref = supabase_url()

    reader = password(24)
    seed(admin_url, reader)
    database_url = f"postgresql://pipeline_reader.{ref}:{reader}@{host}/postgres?sslmode=require"

    print("== Tạo Space và dataset ==")
    api.create_repo(space, repo_type="space", space_sdk="docker", private=False, exist_ok=True)
    api.create_repo(data_repo, repo_type="dataset", private=True, exist_ok=True)
    engineer, reviewer, dagster = password(16), password(16), password(16)
    for key, value in {
        "OPENAI_API_KEY": openai_key,
        "PIPELINE_DATABASE_URL": database_url,
        "PIPELINE_ENGINEER_PASSWORD": engineer,
        "PIPELINE_REVIEWER_PASSWORD": reviewer,
        "DAGSTER_PASSWORD": dagster,
        "HF_TOKEN": token,
    }.items():
        api.add_space_secret(space, key, value)
    api.add_space_variable(space, "DATA_REPO", data_repo)

    backend = "https://" + space.lower().replace("/", "-").replace("_", "-").replace(".", "-") + ".hf.space"
    print(f"""
Xong. Lưu lại (chỉ hiện một lần):
  Engineer: engineer / {engineer}
  Reviewer: reviewer / {reviewer}
  Dagster:  {backend}/dagster   (admin / {dagster})

Bước tiếp theo (docs/deploy-free.md):
  GitHub → Settings → Secrets and variables → Actions:
    HF_TOKEN = token vừa nhập      HF_SPACE = {space}
    rồi Actions → Deploy Space → Run workflow.
  Vercel → Add New Project → repo này, Root Directory = web, Environment Variable:
    PIPELINE_BACKEND_URL = {backend}
""")


if __name__ == "__main__":
    main()
