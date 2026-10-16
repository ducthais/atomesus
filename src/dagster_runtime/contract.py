"""Nhận diện bundle template v2 bằng AST, không thực thi file pipeline (chỉ dùng thư viện chuẩn).

Bundle hợp lệ có đúng một phép gán `RUNTIME_CONTRACT = <số nguyên>` trực tiếp ở cấp module của
`infra/dagster/generated/<model>_asset.py` (file nằm trong files_hash nên đã được ký duyệt). Mọi chỗ khác gán / xóa /
khai báo lại tên này (trong if, hàm, class, `+=`, `for`, `with`, `:=`, `import ... as`, `def`, `global`…) làm bundle bị
coi là không rõ hợp đồng. Checker không bắt được ghi động kiểu `globals()[...]`; không sao, vì không thành phần nào đọc
giá trị lúc chạy: loader, supervisor và Day 2 chỉ tin kết quả AST ở đây.
"""

import ast
from pathlib import Path

from src.dagster_runtime import RUNTIME_CONTRACT

NAME = "RUNTIME_CONTRACT"


def is_project_dir(name: str) -> bool:
    """Thư mục project thật: không ẩn, không phải bản tạm/sao lưu `<model>.tmp-<token>`/`<model>.old-<token>`."""
    return not (name.startswith(".") or ".tmp-" in name or ".old-" in name)


def asset_module(project: Path) -> Path:
    """File code location của bundle (src/codegen/render.py: `infra/dagster/generated/<model>_asset.py`)."""
    return project / "infra" / "dagster" / "generated" / f"{project.name}_asset.py"


def _binds(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == NAME and isinstance(node.ctx, ast.Store | ast.Del)
    if isinstance(node, ast.alias):
        return (node.asname or node.name.split(".")[0]) == NAME
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name == NAME
    if isinstance(node, ast.Global | ast.Nonlocal):
        return NAME in node.names
    if isinstance(node, ast.arg):
        return node.arg == NAME
    if isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
        return node.name == NAME
    if isinstance(node, ast.MatchMapping):
        return node.rest == NAME
    return False


def runtime_contract(source: str) -> int | None:
    """Giá trị `RUNTIME_CONTRACT` của một file Python; None nếu thiếu, không phải literal int, hoặc bị gán lại."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    top = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == NAME
    ]
    if len(top) != 1 or sum(1 for node in ast.walk(tree) if _binds(node)) != 1:
        return None
    value = top[0].value
    return value.value if isinstance(value, ast.Constant) and type(value.value) is int else None


def bundle_contract(project: Path) -> int | None:
    """Hợp đồng runtime của một project đã cài; None nếu không có file asset hoặc không đọc được."""
    try:
        return runtime_contract(asset_module(project).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None


def is_legacy(project: Path) -> bool:
    """Bundle template cũ còn nằm trên đĩa: có file asset nhưng không mang hợp đồng hiện tại. Chỉ giữ lịch sử."""
    return asset_module(project).is_file() and bundle_contract(project) != RUNTIME_CONTRACT


def current_projects(deploy_dir: str | Path) -> list[Path]:
    """Project đang cài mang hợp đồng hiện tại, theo tên; bỏ thư mục tạm/sao lưu và bundle cũ."""
    root = Path(deploy_dir)
    if not root.is_dir():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and is_project_dir(path.name) and bundle_contract(path) == RUNTIME_CONTRACT
    )
