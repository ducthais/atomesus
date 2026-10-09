"""Giữ data/ qua các lần Hugging Face Space khởi động lại (docs/deploy-free.md).

Ổ đĩa của Space miễn phí bị xóa mỗi lần build lại hoặc thức dậy sau khi ngủ, nên data/ (sổ bằng chứng, chat, warehouse
DuckDB, project Dagster đã duyệt, lịch sử Dagster) được sao lưu vào một dataset riêng tư trên Hugging Face.

  python scripts/space_sync.py restore   # lúc khởi động: tải bản sao lưu mới nhất về data/
  python scripts/space_sync.py loop      # chạy nền: sao lưu mỗi SYNC_SECONDS, sao lưu lần cuối khi nhận SIGTERM

Cần HF_TOKEN (quyền write) và DATA_REPO (vd. ten-ban/atomesus-data). Thiếu thì bỏ qua: app vẫn chạy, dữ liệu mất khi
Space khởi động lại.
"""

import os
import shutil
import signal
import sqlite3
import sys
import tempfile
import threading
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SQLITE_HEADER = b"SQLite format 3\x00"
SKIP_SUFFIXES = (".log", ".log.err", ".pyc")


def _repo() -> tuple[str, str] | None:
    token, repo = os.environ.get("HF_TOKEN", ""), os.environ.get("DATA_REPO", "")
    if not token or not repo:
        print("space_sync: thiếu HF_TOKEN / DATA_REPO, data/ không được sao lưu.", flush=True)
        return None
    return token, repo


def restore() -> None:
    from huggingface_hub import HfApi, snapshot_download

    if not (cfg := _repo()):
        return
    token, repo = cfg
    api = HfApi(token=token)
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    if not api.list_repo_files(repo, repo_type="dataset"):
        print(f"space_sync: {repo} còn trống, bắt đầu với data/ rỗng.", flush=True)
        return
    snapshot_download(repo, repo_type="dataset", local_dir=DATA, token=token)
    shutil.rmtree(DATA / ".cache", ignore_errors=True)  # thư mục đánh dấu của snapshot_download
    print(f"space_sync: đã khôi phục data/ từ {repo}.", flush=True)


def _snapshot(dest: Path) -> None:
    """Chép data/ sang dest. File SQLite (lịch sử Dagster) chép bằng backup API để không lấy phải bản đang ghi dở."""
    for src in DATA.rglob("*"):
        rel = src.relative_to(DATA)
        if (
            not src.is_file()
            or rel.parts[0] == ".cache"
            or "__pycache__" in rel.parts
            or src.name.endswith(SKIP_SUFFIXES)
        ):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with src.open("rb") as f:
                is_sqlite = f.read(16) == SQLITE_HEADER
            if is_sqlite:
                with closing(sqlite3.connect(src)) as source, closing(sqlite3.connect(target)) as copy:
                    source.backup(copy)
            else:
                shutil.copy2(src, target)
        except (OSError, sqlite3.Error):
            continue  # file vừa bị xóa / đang khóa: lần sau chép tiếp


def backup() -> None:
    from huggingface_hub import HfApi

    if not (cfg := _repo()) or not DATA.exists():
        return
    token, repo = cfg
    api = HfApi(token=token)
    with tempfile.TemporaryDirectory() as tmp:
        _snapshot(Path(tmp))
        # delete_patterns: file đã xóa ở data/ cũng bị xóa khỏi bản sao lưu.
        api.upload_folder(
            folder_path=tmp, repo_id=repo, repo_type="dataset", delete_patterns="*", commit_message="backup data/"
        )
    api.super_squash_history(repo, repo_type="dataset")  # chỉ giữ bản mới nhất, dataset không phình theo thời gian
    print("space_sync: đã sao lưu data/.", flush=True)


def loop() -> None:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    every = int(os.environ.get("SYNC_SECONDS", "600"))
    while not stop.wait(every):
        try:
            backup()
        except Exception as exc:  # mạng chập chờn: thử lại lần sau, không làm sập Space
            print(f"space_sync: sao lưu lỗi: {type(exc).__name__}: {exc}", flush=True)
    backup()


if __name__ == "__main__":
    commands = {"restore": restore, "backup": backup, "loop": loop}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit(f"usage: python scripts/space_sync.py {'|'.join(commands)}")
    commands[sys.argv[1]]()
