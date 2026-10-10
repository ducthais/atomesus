"""Xuất bộ khung repo pipeline từ app; chạy lại chỉ ghi đè các file của bộ khung."""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export_pipeline_repo(destination: Path, source: Path = ROOT) -> list[str]:
    """Sao chép mẫu CI, manifest và scaffold dbt; giữ nguyên mọi file khác ở đích."""
    files = {
        path.relative_to(source / "deploy/pipeline-repo").as_posix(): path.read_bytes()
        for path in sorted((source / "deploy/pipeline-repo").rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    files["ci/manifest.py"] = (source / "src/deployer/manifest.py").read_bytes()
    files["README.md"] = (source / "docs/deploy-pr.md").read_bytes()
    requirements = (source / "requirements.txt").read_text(encoding="utf-8").splitlines()
    versions = []
    for name in ("dbt-core", "dbt-duckdb"):
        matches = [line.split("#", 1)[0].strip() for line in requirements if re.match(rf"^{name}\s*[=<>!~]", line)]
        if len(matches) != 1:
            raise ValueError(f"requirements.txt: cần đúng một ràng buộc phiên bản cho {name}.")
        versions += matches
    files["requirements-ci.txt"] = (
        "# Sinh từ requirements.txt của app; không sửa riêng.\n" + "\n".join(versions) + "\n"
    ).encode()
    scaffold = source / "dbt_project"
    for path in sorted(scaffold.rglob("*")):
        relative = path.relative_to(scaffold)
        if {"target", "logs", "__pycache__"} & set(relative.parts) or path.name.endswith(
            (".duckdb", ".duckdb.wal", ".pyc")
        ):
            continue
        if path.is_symlink():
            raise ValueError(f"{path}: scaffold không được chứa symlink.")
        if path.is_file():
            files[f"_scaffold/dbt_project/{relative.as_posix()}"] = path.read_bytes()
    destination = destination.absolute()
    for name in files:
        target = destination / name
        for entry in (target, *target.parents):
            if entry.is_symlink():
                raise ValueError(f"{entry}: đích xuất không được đi qua symlink.")
    for name, content in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="Thư mục repo pipeline đã clone.")
    args = parser.parse_args()
    try:
        written = export_pipeline_repo(args.destination)
    except (OSError, ValueError) as exc:
        print(f"Không xuất được bộ khung: {exc}", file=sys.stderr)
        return 1
    print(f"Đã xuất {len(written)} file vào {args.destination}.")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
