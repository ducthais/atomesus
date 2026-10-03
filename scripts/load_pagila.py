"""Nạp bộ dữ liệu mẫu Pagila (cho thuê DVD) vào Postgres nguồn của project, schema `pagila`.

Repo kèm sẵn bản dump `infra/postgres/init/003_pagila.sql.gz` (đúng dữ liệu các con số trong docs/demo.md dựa vào):
- Postgres **mới tạo** (volume trống) tự nạp file đó lúc khởi động, không cần làm gì.
- Postgres **đã có từ trước** (volume cũ) thì chạy script này để nạp vào container đang chạy.

    python scripts/load_pagila.py                    # nạp bản dump trong repo
    python scripts/load_pagila.py --replace          # xóa schema pagila cũ rồi nạp lại
    python scripts/load_pagila.py --from <thư mục>   # nạp từ 6 file Pagila gốc (1. pagila-schema.sql …)

Nguồn `pagila_pg` (domain `rental`) đã khai báo trong policy/domains.yml.
"""

import argparse
import gzip
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "infra" / "postgres" / "init"
DUMP, GRANTS_FILE = INIT / "003_pagila.sql.gz", INIT / "004_pagila_grants.sql"
CONTAINER, DATABASE, ADMIN = "p045-b-postgres-1", "pipeline_demo", "pipeline_admin"
FILES = [
    "1. pagila-schema.sql",
    "2. pagila-insert-data.sql",
    "3. release_year_update.sql",
    "4. before_additional_sakila_data.sql",
    "5. additional_sakila_data.sql",
    "6. get_customer_balance.sql",
]


def rewrite(text: str) -> str:
    """public → pagila, bỏ OWNER TO postgres, luôn đặt search_path vì mỗi file chạy trong một phiên psql riêng."""
    text = re.sub(r"(?m)^ALTER .* OWNER TO postgres;\s*$", "", text)
    text = text.replace("SET search_path = public, pg_catalog;", "SET search_path = pagila, pg_catalog;")
    text = re.sub(r"\bpublic\.", "pagila.", text)
    return "SET search_path = pagila, pg_catalog;\n" + text


def psql(container: str, *args: str) -> subprocess.CompletedProcess:
    command = ["docker", "exec", container, "psql", "-v", "ON_ERROR_STOP=1", "-q", "-At", "-U", ADMIN, "-d", DATABASE]
    return subprocess.run([*command, *args], capture_output=True, text=True, encoding="utf-8")


def run_file(container: str, sql: str, label: str, tmp: Path) -> bool:
    """Chép một file SQL vào container rồi chạy bằng psql (tránh giới hạn độ dài dòng lệnh trên Windows)."""
    local = tmp / "load.sql"
    local.write_text(sql, encoding="utf-8")
    subprocess.run(["docker", "cp", str(local), f"{container}:/tmp/pagila_load.sql"], check=True)
    done = psql(container, "-f", "/tmp/pagila_load.sql")
    if done.returncode != 0:
        print(f"Lỗi ở {label}:\n{done.stderr[-2000:]}", file=sys.stderr)
        return False
    print(f"✓ {label}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path, help="thư mục chứa 6 file Pagila gốc thay vì bản dump")
    parser.add_argument("--replace", action="store_true", help="xóa schema pagila hiện có rồi nạp lại")
    parser.add_argument("--container", default=CONTAINER, help=f"container Postgres (mặc định {CONTAINER})")
    args = parser.parse_args()
    container = args.container

    if args.source:
        missing = [name for name in FILES if not (args.source / name).is_file()]
        if missing:
            print(f"Thiếu file trong {args.source}: {missing}", file=sys.stderr)
            return 2
    if subprocess.run(["docker", "inspect", container], capture_output=True).returncode != 0:
        print(f"Không thấy container {container}: bật Postgres trước (xem README).", file=sys.stderr)
        return 2
    exists = psql(container, "-c", "SELECT 1 FROM pg_namespace WHERE nspname = 'pagila'").stdout.strip() == "1"
    if exists and not args.replace:
        print("Schema pagila đã có. Thêm --replace để xóa và nạp lại.")
        return 0
    if exists:
        psql(container, "-c", "DROP SCHEMA pagila CASCADE;")

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        if args.source:
            psql(container, "-c", "CREATE SCHEMA pagila;")
            for name in FILES:
                if not run_file(container, rewrite((args.source / name).read_text(encoding="utf-8")), name, tmp):
                    return 1
        elif not run_file(container, gzip.decompress(DUMP.read_bytes()).decode("utf-8"), DUMP.name, tmp):
            return 1
        if not run_file(container, GRANTS_FILE.read_text(encoding="utf-8"), GRANTS_FILE.name, tmp):
            return 1
    tables = psql(
        container,
        "-c",
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'pagila' AND c.relkind IN ('r', 'p') AND NOT c.relispartition",
    ).stdout.strip()
    payments = psql(
        container, "-c", "SELECT count(*) || ' giao dịch, tổng ' || sum(amount) FROM pagila.payment"
    ).stdout.strip()
    print(f"Xong: {tables} bảng trong schema pagila ({payments}), đã cấp quyền đọc cho pipeline_reader.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
