"""Code location hợp nhất `atomesus` (lỗi #4): một code server nạp mọi pipeline đã duyệt mang template v2.

Chạy bởi scripts/dagster_local.py: `dagster code-server start -m src.dagster_runtime.loader --location-name atomesus`.
Cài pipeline mới thì supervisor gọi GraphQL `reloadRepositoryLocation`: code server nạp lại module này, run đang chạy
không bị dừng, không bật thêm tiến trình nào.

- Bundle template cũ (không có `RUNTIME_CONTRACT = 2`, đọc bằng AST) bị bỏ qua trước khi import: chỉ giữ lịch sử, không
  để chúng đọc credential lúc import hay ghi đè WAREHOUSE_PATH của pipeline khác.
- Bundle lỗi nạp hoặc gộp không được (trùng job, trùng key) bị bỏ qua; các bundle còn lại vẫn nạp. Deployer thấy job vắng
  sau khi nạp lại thì báo lỗi ngay (src/deployer/verify.py).
- Env của code server không có credential nguồn nào (`SOURCES__*`): run đọc credential của đúng pipeline đó lúc chạy
  (src/dagster_runtime/execute.py).
"""

import importlib.util
import os
import sys
from pathlib import Path

from dagster import Definitions

from src.dagster_runtime import DEPLOY_DIR_ENV, RUNTIME_CONTRACT
from src.dagster_runtime.contract import asset_module, bundle_contract, is_project_dir


def _import(project: Path):
    name = f"atomesus_pipeline_{project.name}"
    spec = importlib.util.spec_from_file_location(name, asset_module(project))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if not isinstance(getattr(module, "defs", None), Definitions):
        sys.modules.pop(name, None)
        raise TypeError("module không có `defs` kiểu Definitions")
    return module.defs


def load_definitions(deploy_dir: str | Path) -> tuple[Definitions, dict[str, str]]:
    """(Definitions đã gộp, {model bị bỏ qua: lý do}). Thứ tự nạp theo tên thư mục để kết quả ổn định."""
    root = Path(deploy_dir)
    projects = sorted(p for p in root.iterdir() if p.is_dir() and is_project_dir(p.name)) if root.is_dir() else []
    merged, skipped = Definitions(), {}
    for project in projects:
        if not asset_module(project).is_file():
            continue
        if bundle_contract(project) != RUNTIME_CONTRACT:
            skipped[project.name] = "template cũ, không nạp (chỉ giữ lịch sử)"
            continue
        try:
            candidate = Definitions.merge(merged, _import(project))
            candidate.get_repository_def()  # trùng job / asset key thì lỗi ở đây, không làm hỏng các bundle trước
        except Exception as exc:
            skipped[project.name] = f"nạp lỗi ({type(exc).__name__})"
            continue
        merged = candidate
    return merged, skipped


defs, SKIPPED = load_definitions(os.environ.get(DEPLOY_DIR_ENV, "data/dagster_local"))
for _model, _reason in SKIPPED.items():
    print(f"Code location: bỏ qua bundle {_model}: {_reason}", flush=True)
