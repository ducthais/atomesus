"""Chặn biểu thức nguy hiểm trước khi render (tạm thay cổng `ast_guard`, SPEC §10, đến khi có sqlglot).

`metrics[].expr`, `filters`, `reject_if` do LLM viết và được chèn nguyên văn vào SQL của dbt, nên:
- không được có `{`/`}`: dbt render Jinja trong file SQL, chèn `{{ ... }}` là chạy được macro (đọc env, chạy query);
- không được có `;` hay comment: không cho nối thêm lệnh hoặc che phần còn lại;
- chỉ gọi hàm trong danh sách cho phép: chặn `read_csv(...)`, `getenv(...)`… đọc file/biến môi trường;
- không có từ khóa ghi/sửa dữ liệu.
Mọi chuỗi khác của Spec (định dạng ngày, đường dẫn…) cũng không được chứa `{`/`}` vì cùng lý do.
"""

import re

from src.spec.models import Spec

STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
FUNCTION_CALL = re.compile(r"\b([a-z_][a-z0-9_]*)\s*\(", re.IGNORECASE)
FORBIDDEN_WORDS = re.compile(
    r"\b(select|insert|update|delete|drop|alter|create|attach|detach|copy|pragma|install|load|call|"
    r"export|import|set|grant|truncate|union)\b",
    re.IGNORECASE,
)
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
        code = STRING_LITERAL.sub("''", expression)
        if ";" in code or "--" in code or "/*" in code:
            violations.append(f"{where}: không được chứa ';' hoặc comment")
        if word := FORBIDDEN_WORDS.search(code):
            violations.append(f"{where}: từ khóa '{word.group(1)}' không được phép trong biểu thức")
        for name in FUNCTION_CALL.findall(code):
            if name.lower() not in ALLOWED_FUNCTIONS:
                violations.append(f"{where}: hàm '{name}' không nằm trong danh sách cho phép")
    return violations
