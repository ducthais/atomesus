"""P15 deploy (bước tạm của Sprint 1): ghi bộ file đã duyệt thành một project Dagster local.

Mỗi pipeline một thư mục `<deploy_dir>/<model_name>/`, gồm scaffold dbt + đúng bộ file Reviewer đã duyệt.
Duyệt lại cùng model thì thay toàn bộ thư mục. Sprint 3 thay bước này bằng pull request.
"""

import shutil
from pathlib import Path

from src.codegen.sandbox import prepare_project
from src.spec.models import Spec


def write_workspace(deploy_dir: str) -> Path:
    """Ghi `<deploy_dir>/workspace.yaml`: mỗi project đã deploy là một code location của Dagster.

    `make dagster` nạp file này; deploy thêm pipeline thì bấm "Reload" trong Dagster UI là thấy.
    """
    root = Path(deploy_dir)
    root.mkdir(parents=True, exist_ok=True)
    lines = ["# Sinh tự động bởi src/codegen/deploy.py; không sửa tay.", "load_from:"]
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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def deploy_local(spec: Spec, files: dict[str, str], deploy_dir: str) -> Path:
    """Ghi project ra đĩa, trả đường dẫn thư mục project."""
    target = Path(deploy_dir) / spec.transform.model_name
    staging = target.with_name(target.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    prepare_project(staging, files)
    # Thay thư mục cũ sau khi bản mới đã ghi xong, để không bao giờ còn lại project ghi dở.
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    write_workspace(deploy_dir)
    return target
