"""Chặn biểu thức nguy hiểm trước khi render bằng AST SQL (SPEC §10).

`metrics[].expr`, `filters`, `reject_if` do LLM viết và được chèn nguyên văn vào SQL của dbt, nên:
- không được có `{`/`}`: dbt render Jinja trong file SQL, chèn `{{ ... }}` là chạy được macro (đọc env, chạy query);
- không được có `;` hay comment: không cho nối thêm lệnh hoặc che phần còn lại;
- chỉ gọi hàm trong danh sách cho phép: chặn `read_csv(...)`, `getenv(...)`… đọc file/biến môi trường;
- không có từ khóa ghi/sửa dữ liệu.
Mọi chuỗi khác của Spec (định dạng ngày, đường dẫn…) cũng không được chứa `{`/`}` vì cùng lý do.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from src.spec.models import Spec

SQL_DIALECT = "duckdb"
ALLOWED_FUNCTIONS = frozenset(
    {
        # tổng hợp
        "sum", "count", "avg", "min", "max", "median", "stddev", "variance", "any_value",
        # vô hướng
        "coalesce", "nullif", "abs", "round", "floor", "ceil", "greatest", "least",
        "lower", "upper", "trim", "length", "date_trunc", "extract", "cast", "try_cast",
        # từ khóa SQL đứng trước dấu ngoặc
        "in", "and", "or", "not", "when", "then", "else", "case", "as", "is", "distinct", "filter", "where",
    }
)  # fmt: skip
FORBIDDEN_EXPRESSIONS = (exp.Query, exp.DML, exp.DDL, exp.Command)


def _function_name(node: exp.Func) -> str:
    return node.name.lower() if isinstance(node, exp.Anonymous) else node.sql_name().lower()


def _expression_violations(expression: str) -> list[str]:
    try:
        tree = sqlglot.parse_one(expression, read=SQL_DIALECT, into=exp.Expr)
    except SqlglotError:
        return ["không phân tích được biểu thức SQL"]
    if tree is None:
        return ["biểu thức SQL rỗng"]

    violations = []
    for node in tree.walk():
        if node.comments:
            violations.append("không được chứa comment SQL")
            break
    if any(isinstance(node, FORBIDDEN_EXPRESSIONS) for node in tree.walk()):
        violations.append("câu lệnh truy vấn hoặc thay đổi dữ liệu không được phép trong biểu thức")
    for node in tree.find_all(exp.Func):
        name = _function_name(node)
        if name not in ALLOWED_FUNCTIONS:
            violations.append(f"hàm '{name}' không nằm trong danh sách cho phép")
    return violations


def _expressions(spec: Spec) -> list[tuple[str, str]]:
    found = [(f"transform.metrics[{m.name}].expr", m.expr) for m in spec.transform.metrics]
    found += [(f"transform.filters[{i}]", f) for i, f in enumerate(spec.transform.filters)]
    for source in spec.sources:
        found += [(f"sources[{source.id}].staging.reject_if[{i}]", r) for i, r in enumerate(source.staging.reject_if)]
    return found


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list | tuple):
        return [s for item in value for s in _strings(item)]
    return []


def expression_violations(spec: Spec) -> list[str]:
    """Danh sách vi phạm; rỗng nghĩa là mọi biểu thức của Spec được phép render."""
    violations = []
    if any("{" in s or "}" in s for s in _strings(spec.model_dump(by_alias=True))):
        violations.append("Spec có ký tự '{' hoặc '}' (cú pháp Jinja của dbt), không được phép")
    for where, expression in _expressions(spec):
        violations.extend(f"{where}: {issue}" for issue in _expression_violations(expression))
    return violations
