"""Đọc nguồn `filesystem` khai báo trong domains.yml (thư mục file CSV). Chỉ đọc, không ra ngoài thư mục gốc của repo.

Mỗi thư mục con có file `*.csv` là một đối tượng (tên = tên thư mục, SPEC §8.4), đọc mọi file theo thứ tự tên.
CSV không có kiểu: ingestion đọc mọi cột thành chuỗi (`read_csv_duckdb(all_varchar=True)`), staging lo ép kiểu, định dạng
ngày, đổi tiền như với cột chuỗi của Postgres. Vì vậy schema báo mọi cột là `text`; dòng mẫu là giá trị gốc trong file.

CSV không có khóa chính: gợi ý cột `<tên số ít>_id` (thư mục `refunds` → `refund_id`) nếu có, để đối soát và khử trùng
có khóa (file xuất theo tháng hay lặp lại bản ghi của tháng trước).
"""

import csv
from collections.abc import Iterator
from pathlib import Path

from src.pipeline.postgres import SourceUnavailableError
from src.policy.models import SourceDecl

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_LIMIT = 1000  # số dòng đầu đọc để lấy danh sách cột (SPEC §12.1)


class FilesUnavailableError(SourceUnavailableError):
    """Thư mục nguồn không đọc được. Là SourceUnavailableError để mọi chỗ đang bắt lỗi nguồn bắt được luôn."""


def root(decl: SourceDecl) -> Path:
    """Thư mục gốc của nguồn: `root` trong domains.yml, tính từ thư mục repo, không được trỏ ra ngoài repo."""
    path = (PROJECT_ROOT / decl.root).resolve()
    if PROJECT_ROOT not in path.parents and path != PROJECT_ROOT:
        raise FilesUnavailableError(f"Thư mục nguồn '{decl.root}' nằm ngoài repo.")
    if not path.is_dir():
        raise FilesUnavailableError(f"Không có thư mục nguồn '{decl.root}' (connection_ref {decl.connection_ref}).")
    return path


def bucket_url(decl: SourceDecl) -> str:
    """URL `file://` cho dlt filesystem."""
    return root(decl).as_uri()


def _files(decl: SourceDecl, name: str) -> list[Path]:
    base = root(decl)
    folder = (base / name).resolve()
    if folder.parent != base or (decl.tables is not None and name not in decl.tables):
        raise FilesUnavailableError("Bảng không nằm trong thư mục nguồn hoặc ngoài các bảng đã chọn.")
    paths = sorted(folder.glob("*.csv"))
    if any(path.resolve().parent != folder for path in paths):
        raise FilesUnavailableError("File CSV nằm ngoài thư mục bảng.")
    return paths


def suggested_key(name: str, columns: list[str]) -> list[str]:
    singular = name[:-1] if name.endswith("s") else name
    for candidate in (f"{singular}_id", f"{name}_id", "id"):
        if candidate in columns:
            return [candidate]
    return []


def objects(decl: SourceDecl) -> dict[str, dict]:
    """Tên đối tượng → {path_pattern, format, columns, primary_key, files, bytes}."""
    found = {}
    for folder in sorted(p for p in root(decl).iterdir() if p.is_dir()):
        if decl.tables is not None and folder.name not in decl.tables:
            continue
        files = _files(decl, folder.name)
        if not files:
            continue
        with files[0].open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), [])
        found[folder.name] = {
            "path_pattern": f"{folder.name}/*.csv",
            "format": "csv",
            "columns": [{"name": column, "type": "text"} for column in header],
            "primary_key": suggested_key(folder.name, header),
            "files": [f.name for f in files],
            "bytes": sum(f.stat().st_size for f in files),
        }
    return found


def records(decl: SourceDecl, name: str, limit: int | None = None) -> Iterator[dict]:
    """Bản ghi của mọi file trong thư mục, theo thứ tự tên file rồi thứ tự dòng; ô rỗng thành None (như CSV reader)."""
    seen = 0
    for path in _files(decl, name):
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                yield {key: value if value != "" else None for key, value in row.items()}
                seen += 1
                if limit is not None and seen >= limit:
                    return


def sample(decl: SourceDecl, name: str, n: int) -> list[dict]:
    return list(records(decl, name, limit=n))
