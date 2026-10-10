"""Cài một pipeline đã duyệt vào thư mục code location của Dagster.

Mỗi pipeline một thư mục `<deploy_dir>/<model_name>/`, gồm scaffold dbt + đúng bộ file Reviewer đã duyệt. Cài lại
cùng model thì thay nguyên tử toàn bộ thư mục; lỗi giữa chừng thì khôi phục bản đang chạy. Dùng cho Dagster local
(scripts/dagster_local.py) và service deployer (cài từ nhánh main của repo pipeline sau khi đã kiểm manifest).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from src.codegen.sandbox import prepare_project
from src.deployer.manifest import project_dir

DBT_PARSE_TIMEOUT_SEC = 120


def write_workspace(deploy_dir: str) -> Path:
    """Ghi `<deploy_dir>/workspace.yaml`: mỗi project đã cài là một code location của Dagster.

    `make dagster` nạp file này; cài thêm pipeline thì scripts/dagster_local.py tự khởi động lại Dagster.
    """
    root = Path(deploy_dir)
    root.mkdir(parents=True, exist_ok=True)
    lines = ["# Sinh tự động bởi src/deployer/install.py; không sửa tay.", "load_from:"]
    for module in sorted(root.glob("*/infra/dagster/generated/*_asset.py")):
        project = module.parents[3]
        lines += [
            "  - python_module:",
            f"      module_name: infra.dagster.generated.{module.stem}",
            f"      working_directory: ./{project.name}",
            f"      location_name: {project.name}",
        ]
    if len(lines) == 2:
        lines[-1] = "load_from: []"
    path = root / "workspace.yaml"
    temporary = root / f".workspace-{uuid4().hex}.tmp"
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def _compile_manifest(project_root: Path) -> None:
    """Biên dịch trước manifest.json cho dbt để Dagster nạp được code location ngay."""
    dbt_dir = project_root / "dbt_project"
    warehouse = Path("data/warehouse/warehouse.duckdb").resolve()
    env = {
        **os.environ,
        "WAREHOUSE_PATH": str(warehouse),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    done = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "parse", "--project-dir", str(dbt_dir), "--profiles-dir", str(dbt_dir)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=DBT_PARSE_TIMEOUT_SEC,
    )
    if done.returncode:
        detail = (done.stderr or done.stdout).strip()[-1000:]
        raise OSError(f"dbt parse thất bại (mã {done.returncode}): {detail}")


def install_project(model_name: str, files: dict[str, str], deploy_dir: str) -> Path:
    """Ghi project ra đĩa, trả đường dẫn thư mục project."""
    project_dir(model_name)  # chặn tên model lạ trước khi ghép đường dẫn
    target = Path(deploy_dir) / model_name
    token = uuid4().hex
    staging = target.with_name(f"{target.name}.tmp-{token}")
    backup = target.with_name(f"{target.name}.old-{token}")
    old_moved = new_active = False
    try:
        prepare_project(staging, files)
        _compile_manifest(staging)
        if target.exists():
            target.rename(backup)
            old_moved = True
        staging.rename(target)
        new_active = True
        write_workspace(deploy_dir)
    except (OSError, subprocess.SubprocessError):
        if new_active and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if old_moved and backup.exists():
            backup.rename(target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    return target
