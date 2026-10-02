"""Nạp bộ dữ liệu mẫu Pagila (cho thuê DVD) vào Postgres nguồn của project, schema `pagila`.

Dump gốc viết cho schema `public` và owner `postgres`; script đổi sang schema `pagila`, bỏ các dòng
`OWNER TO postgres`, chạy 6 file theo thứ tự rồi cấp quyền SELECT cho `pipeline_reader`.
Nguồn `pagila_pg` (domain `rental`) đã khai báo trong policy/domains.yml.

    python scripts/load_pagila.py <thư mục pagila>            # ví dụ C:/Users/<bạn>/Downloads/pagila
    python scripts/load_pagila.py <thư mục pagila> --replace  # xóa schema pagila cũ rồi nạp lại
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CONTAINER, DATABASE, ADMIN = "p045-b-postgres-1", "pipeline_demo", "pipeline_admin"
FILES = [
    "1. pagila-schema.sql",
    "2. pagila-insert-data.sql",
    "3. release_year_update.sql",
    "4. before_additional_sakila_data.sql",
    "5. additional_sakila_data.sql",
    "6. get_customer_balance.sql",
]
GRANTS = (
    "GRANT USAGE ON SCHEMA pagila TO pipeline_reader;"
    "GRANT SELECT ON ALL TABLES IN SCHEMA pagila TO pipeline_reader;"
    "ALTER DEFAULT PRIVILEGES IN SCHEMA pagila GRANT SELECT ON TABLES TO pipeline_reader;"
)


def rewrite(text: str) -> str:
    """public → pagila, bỏ OWNER TO postgres, luôn đặt search_path vì mỗi file chạy trong một phiên psql riêng."""
    text = re.sub(r"(?m)^ALTER .* OWNER TO postgres;\s*$", "", text)
    text = text.replace("SET search_path = public, pg_catalog;", "SET search_path = pagila, pg_catalog;")
    text = re.sub(r"\bpublic\.", "pagila.", text)
    return "SET search_path = pagila, pg_catalog;\n" + text


def psql(*args: str) -> subprocess.CompletedProcess:
    command = ["docker", "exec", CONTAINER, "psql", "-v", "ON_ERROR_STOP=1", "-q", "-At", "-U", ADMIN, "-d", DATABASE]
    return subprocess.run([*command, *args], capture_output=True, text=True, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="thư mục chứa các file pagila (1. pagila-schema.sql …)")
    parser.add_argument("--replace", action="store_true", help="xóa schema pagila hiện có rồi nạp lại")
    args = parser.parse_args()

    missing = [name for name in FILES if not (args.source / name).is_file()]
    if missing:
        print(f"Thiếu file trong {args.source}: {missing}", file=sys.stderr)
        return 2
    exists = psql("-c", "SELECT 1 FROM pg_namespace WHERE nspname = 'pagila'").stdout.strip() == "1"
    if exists and not args.replace:
        print("Schema pagila đã có. Thêm --replace để xóa và nạp lại.", file=sys.stderr)
        return 1
    if exists:
        psql("-c", "DROP SCHEMA pagila CASCADE;")
    psql("-c", "CREATE SCHEMA pagila;")

    with tempfile.TemporaryDirectory() as tmp:
        for index, name in enumerate(FILES, 1):
            local = Path(tmp) / f"{index}.sql"
            local.write_text(rewrite((args.source / name).read_text(encoding="utf-8")), encoding="utf-8")
            subprocess.run(["docker", "cp", str(local), f"{CONTAINER}:/tmp/pagila_{index}.sql"], check=True)
            done = psql("-f", f"/tmp/pagila_{index}.sql")
            if done.returncode != 0:
                print(f"Lỗi ở {name}:\n{done.stderr[-2000:]}", file=sys.stderr)
                return 1
            print(f"✓ {name}")
    if psql("-c", GRANTS).returncode != 0:
        print("Cấp quyền cho pipeline_reader thất bại.", file=sys.stderr)
        return 1
    tables = psql(
        "-c",
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'pagila' AND c.relkind IN ('r', 'p') AND NOT c.relispartition",
    ).stdout.strip()
    print(f"Xong: {tables} bảng trong schema pagila, đã cấp quyền đọc cho pipeline_reader.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
