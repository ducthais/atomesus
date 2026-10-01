"""P15 deploy (bước tạm của Sprint 1): ghi bộ file đã duyệt thành một project Dagster local.

Mỗi pipeline một thư mục `<deploy_dir>/<model_name>/`, gồm scaffold dbt + đúng bộ file Reviewer đã duyệt.
Duyệt lại cùng model thì thay toàn bộ thư mục. Sprint 3 thay bước này bằng pull request.
"""

import shutil
from pathlib import Path

from src.codegen.sandbox import prepare_project
from src.spec.models import Spec


def deploy_local(spec: Spec, files: dict[str, str], deploy_dir: str) -> Path:
    """Ghi project ra đĩa, trả đường dẫn thư mục project."""
    target = Path(deploy_dir) / spec.transform.model_name
    staging = target.with_name(target.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    prepare_project(staging, files)
    # Thay thư mục cũ sau khi bản mới đã ghi xong, để không bao giờ còn lại project ghi dở.
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)
    return target
