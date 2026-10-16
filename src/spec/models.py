"""Spec — bản thiết kế pipeline dạng JSON (SPEC §8.1).

Model chỉ kiểm tra những gì suy ra được từ chính Spec. Phần cần dữ liệu ngoài nằm ở chỗ khác:
- đối chiếu chính sách (domain, nguồn, trần join, tỷ giá): `src/spec/policy_check.py`
- cột có thật ở nguồn, kiểu khóa join sau staging, `date_formats` cho cột thô dạng chuỗi: cổng `schema_real` (P7)
- cú pháp SQL của `expr`, `filters`, `reject_if`: cổng `ast_guard` (P7)
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IDENTIFIER = r"^[a-z][a-z0-9_]{0,62}$"
RAW_IDENTIFIER = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"  # tên cột thô ở nguồn, ví dụ `orderId` của REST
COLUMN_TYPE = r"^(bigint|integer|double|varchar|boolean|date|timestamp|decimal\(\d{1,2},\d{1,2}\))$"
QUALIFIED_REF = re.compile(r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b")
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
FILESYSTEM_CURSOR = "modification_date"


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def _refs(expression: str) -> list[tuple[str, str]]:
    """Các tham chiếu `<id>.<cột>` trong một biểu thức, bỏ qua chuỗi trong nháy đơn."""
    return QUALIFIED_REF.findall(STRING_LITERAL.sub("''", expression))


# ---------- sources[].staging ----------


class CurrencyRule(_Spec):
    code_column: str = Field(pattern=RAW_IDENTIFIER)
    to: str = Field(min_length=3, max_length=3)


class StagingColumn(_Spec):
    from_: str = Field(alias="from", pattern=RAW_IDENTIFIER)
    as_: str = Field(alias="as", pattern=IDENTIFIER)
    type: str = Field(pattern=COLUMN_TYPE)
    date_formats: list[str] | None = Field(default=None, min_length=1)
    currency: CurrencyRule | None = None

    @model_validator(mode="after")
    def _options_match_type(self) -> "StagingColumn":
        if self.date_formats and self.type not in ("date", "timestamp"):
            raise ValueError(f"cột '{self.as_}': date_formats chỉ dùng cho kiểu date/timestamp")
        if self.currency and not (self.type.startswith("decimal") or self.type == "double"):
            raise ValueError(f"cột '{self.as_}': currency chỉ dùng cho kiểu số thập phân")
        return self


class Dedupe(_Spec):
    keys: list[str] | None = Field(default=None, min_length=1)  # mặc định bằng primary_key
    keep: Literal["latest", "first"]
    order_by: str = Field(pattern=RAW_IDENTIFIER)


class Staging(_Spec):
    columns: list[StagingColumn] = Field(min_length=1)
    dedupe: Dedupe | None = None
    reject_if: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outputs_unique(self) -> "Staging":
        names = [c.as_ for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("staging.columns[].as phải duy nhất trong một nguồn")
        return self


# ---------- sources[] ----------


class Target(_Spec):
    dataset: Literal["bronze"]
    table: str = Field(pattern=IDENTIFIER)


class _SourceBase(_Spec):
    id: str = Field(pattern=IDENTIFIER)
    source_ref: str = Field(pattern=IDENTIFIER)
    primary_key: list[Annotated[str, Field(pattern=RAW_IDENTIFIER)]] = Field(default_factory=list)
    cursor_field: str | None = Field(default=None, pattern=RAW_IDENTIFIER)
    write_disposition: Literal["append", "replace", "merge"]
    target: Target
    staging: Staging

    @model_validator(mode="after")
    def _load_rules(self) -> "_SourceBase":
        if self.write_disposition == "merge" and not self.primary_key:
            raise ValueError(f"nguồn '{self.id}': write_disposition=merge bắt buộc có primary_key")
        if self.write_disposition in ("merge", "append") and not self.cursor_field:
            raise ValueError(f"nguồn '{self.id}': load tăng dần ({self.write_disposition}) bắt buộc có cursor_field")
        return self

    @property
    def outputs(self) -> dict[str, str]:
        """Tên cột đầu ra của staging → kiểu."""
        return {c.as_: c.type for c in self.staging.columns}


class PostgresSource(_SourceBase):
    kind: Literal["postgres"]
    table: str = Field(pattern=IDENTIFIER)


class RestApiSource(_SourceBase):
    kind: Literal["rest_api"]
    endpoint: str = Field(pattern=r"^/\S*$")
    records_path: str = Field(min_length=1)
    pagination: Literal["page", "offset", "cursor", "none"]


class FilesystemSource(_SourceBase):
    kind: Literal["filesystem"]
    path_pattern: str = Field(min_length=1)
    format: Literal["csv", "parquet", "jsonl"]

    @model_validator(mode="after")
    def _cursor_is_mtime(self) -> "FilesystemSource":
        if self.cursor_field not in (None, FILESYSTEM_CURSOR):
            raise ValueError(f"nguồn '{self.id}': filesystem chỉ dùng cursor_field='{FILESYSTEM_CURSOR}'")
        return self


Source = Annotated[PostgresSource | RestApiSource | FilesystemSource, Field(discriminator="kind")]


# ---------- transform ----------


class Join(_Spec):
    source: str = Field(pattern=IDENTIFIER)
    type: Literal["inner", "left"]
    on: list[tuple[str, str]] = Field(min_length=1)  # rỗng = tích Descartes, bị cấm


class Metric(_Spec):
    name: str = Field(pattern=IDENTIFIER)
    expr: str = Field(min_length=1)


class ColumnTest(_Spec):
    column: str = Field(pattern=IDENTIFIER)
    test: Literal["not_null", "unique", "not_negative"]


class TimeGrain(_Spec):
    """Gộp một cột thời gian của grain theo kỳ (date_trunc), thay vì theo từng thời điểm chính xác."""

    column: str = Field(min_length=1)  # đúng một phần tử của grain
    unit: Literal["day", "week", "month", "quarter", "year"]


class Transform(_Spec):
    model_name: str = Field(pattern=IDENTIFIER)
    from_: str = Field(alias="from", pattern=IDENTIFIER)
    joins: list[Join] = Field(default_factory=list)
    materialization: Literal["view", "table", "incremental"]
    unique_key: list[str] = Field(default_factory=list)
    grain: list[str] = Field(min_length=1)
    time_grain: TimeGrain | None = None
    metrics: list[Metric] = Field(min_length=1)
    filters: list[str] = Field(default_factory=list)
    partition_by: str | None = None
    tests: list[ColumnTest] = Field(default_factory=list)

    @model_validator(mode="after")
    def _incremental_needs_key(self) -> "Transform":
        if self.materialization == "incremental" and not self.unique_key:
            raise ValueError("materialization=incremental bắt buộc có unique_key")
        names = [m.name for m in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("metrics[].name phải duy nhất")
        return self

    @property
    def outputs(self) -> set[str]:
        """Cột của model gold: phần sau dấu chấm của grain, cộng tên các metric."""
        return {g.rpartition(".")[2] for g in self.grain} | {m.name for m in self.metrics}


# ---------- Spec ----------


class Schedule(_Spec):
    cron: str = Field(pattern=r"^\S+( \S+){4}$")  # 5 trường cron


class Estimates(_Spec):
    bytes_scanned: int = Field(ge=0)
    usd: float = Field(ge=0)
    latency_sec: float = Field(ge=0)


class Spec(_Spec):
    spec_version: Literal["1.2"]
    domain: str = Field(pattern=IDENTIFIER)
    sources: list[Source] = Field(min_length=1)
    transform: Transform
    schedule: Schedule
    estimates: Estimates

    @model_validator(mode="after")
    def _sources_and_transform_agree(self) -> "Spec":
        outputs = {s.id: s.outputs for s in self.sources}
        _require_unique([s.id for s in self.sources], "sources[].id")
        _require_unique([s.target.table for s in self.sources], "sources[].target.table")
        _check_join_graph(self.transform, outputs)
        _check_column_refs(self.transform, outputs)
        return self


def _require_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} phải duy nhất")


def _check_join_graph(transform: Transform, outputs: dict[str, dict[str, str]]) -> None:
    if transform.from_ not in outputs:
        raise ValueError(f"transform.from '{transform.from_}' không phải một sources[].id")
    joined = [j.source for j in transform.joins]
    expected = set(outputs) - {transform.from_}
    if sorted(joined) != sorted(expected):
        raise ValueError(
            f"mọi nguồn khác transform.from phải xuất hiện đúng một lần trong joins; "
            f"cần {sorted(expected)}, đang có {joined}"
        )
    available = {transform.from_}
    for join in transform.joins:
        for left, right in join.on:
            sides = {_ref_source(left, outputs), _ref_source(right, outputs)}
            if join.source not in sides or not (sides - {join.source}) <= available:
                raise ValueError(f"join '{join.source}': on [{left}, {right}] phải nối nguồn này với nguồn đã có trước")
        available.add(join.source)


def _ref_source(ref: str, outputs: dict[str, dict[str, str]]) -> str:
    source_id, _, column = ref.partition(".")
    if source_id not in outputs:
        raise ValueError(f"'{ref}': '{source_id}' không phải sources[].id; các id hiện có: {sorted(outputs)}")
    if column not in outputs[source_id]:
        raise ValueError(
            f"'{ref}' không phải cột đầu ra staging của '{source_id}' (đang có: {sorted(outputs[source_id])}); "
            f"muốn dùng cột này thì khai báo nó trong staging.columns của nguồn '{source_id}'"
        )
    return source_id


def _check_column_refs(transform: Transform, outputs: dict[str, dict[str, str]]) -> None:
    multi_source = len(outputs) > 1
    for grain in transform.grain:
        if "." in grain:
            _ref_source(grain, outputs)
        elif multi_source:
            raise ValueError(f"grain '{grain}': khi có từ hai nguồn phải viết dạng <id>.<cột>")
        elif grain not in outputs[transform.from_]:
            raise ValueError(
                f"grain '{grain}' không phải cột đầu ra staging (đang có: {sorted(outputs[transform.from_])})"
            )
    if transform.time_grain:
        column = transform.time_grain.column
        if column not in transform.grain:
            raise ValueError(f"time_grain.column '{column}' phải là một phần tử của grain {transform.grain}")
        source_id, _, name = column.rpartition(".")
        kind = outputs[source_id or transform.from_].get(name)
        if kind not in ("date", "timestamp"):
            raise ValueError(
                f"time_grain.column '{column}' phải là cột staging kiểu date hoặc timestamp (đang là {kind})"
            )
    for expression in [m.expr for m in transform.metrics] + transform.filters:
        for source_id, column in _refs(expression):
            _ref_source(f"{source_id}.{column}", outputs)
    gold = transform.outputs
    named = transform.unique_key + [t.column for t in transform.tests]
    if transform.partition_by:
        named.append(transform.partition_by)
    missing = sorted(set(named) - gold)
    if missing:
        raise ValueError(f"unique_key/partition_by/tests dùng cột không có ở gold: {missing}")
