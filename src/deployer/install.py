"""Cài một pipeline đã duyệt vào thư mục code location của Dagster.

Mỗi pipeline một thư mục `<deploy_dir>/<model_name>/`, gồm scaffold dbt + đúng bộ file Reviewer đã duyệt. Cài lại
cùng model thì thay nguyên tử toàn bộ thư mục; lỗi giữa chừng thì khôi phục bản đang chạy. Dùng cho Dagster local
(scripts/dagster_local.py) và service deployer (cài từ nhánh main của repo pipeline sau khi đã kiểm manifest).
"""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from src.codegen.sandbox import PASSTHROUGH_ENV, prepare_project
from src.deployer.manifest import project_dir

DBT_PARSE_TIMEOUT_SEC = 120


def _is_project_dir(name: str) -> bool:
    """Thư mục project thật: không ẩn, không phải bản tạm/sao lưu `<model>.tmp-<token>`/`<model>.old-<token>`."""
    return not (name.startswith(".") or ".tmp-" in name or ".old-" in name)


def write_workspace(deploy_dir: str) -> Path:
    """Ghi `<deploy_dir>/workspace.yaml`: mỗi project đã cài là một code location của Dagster.

    `make dagster` nạp file này; cài thêm pipeline thì scripts/dagster_local.py tự khởi động lại Dagster.
    Bỏ qua thư mục tạm/sao lưu của install_project trong khi chờ xác nhận bản mới.
    """
    root = Path(deploy_dir)
    root.mkdir(parents=True, exist_ok=True)
    lines = ["# Sinh tự động bởi src/deployer/install.py; không sửa tay.", "load_from:"]
    for module in sorted(root.glob("*/infra/dagster/generated/*_asset.py")):
        project = module.parents[3]
        if not _is_project_dir(project.name):
            continue
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
        **{key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ},
        "WAREHOUSE_PATH": str(warehouse),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DO_NOT_TRACK": "1",
    }
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "from dbt.cli.main import cli; cli()",
            "parse",
            "--project-dir",
            str(dbt_dir),
            "--profiles-dir",
            str(dbt_dir),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=DBT_PARSE_TIMEOUT_SEC,
    )
    if done.returncode:
        raise OSError(f"dbt parse thất bại (mã {done.returncode}); kiểm tra bộ file dbt của pipeline.")


def install_project(
    model_name: str, files: dict[str, str], deploy_dir: str, *, verify: Callable[[Path], None] | None = None
) -> Path:
    """Ghi project ra đĩa; giữ bản cũ tới khi verify xác nhận. Lỗi thì khôi phục project và workspace."""
    project_dir(model_name)  # chặn tên model lạ trước khi ghép đường dẫn
    target = Path(deploy_dir) / model_name
    token = uuid4().hex
    staging = target.with_name(f"{target.name}.tmp-{token}")
    backup = target.with_name(f"{target.name}.old-{token}")
    old_moved = new_active = workspace_written = False
    try:
        prepare_project(staging, files)
        _compile_manifest(staging)
        if target.exists():
            target.rename(backup)
            old_moved = True
        staging.rename(target)
        new_active = True
        write_workspace(deploy_dir)
        workspace_written = True
        if verify is not None:
            verify(target)
    except Exception:
        if new_active and target.exists():
            shutil.rmtree(target)
        if old_moved and backup.exists():
            backup.rename(target)
        if workspace_written:
            write_workspace(deploy_dir)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    return target
