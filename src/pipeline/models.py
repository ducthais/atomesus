"""Small staging contract using the Spec's from/as/type column mappings."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IDENTIFIER = r"^[a-z][a-z0-9_]{0,62}$"
# Định dạng strptime: chỉ chỉ thị % và dấu phân cách thường gặp, vì được ghép vào câu SQL DuckDB.
DATE_FORMAT = r"^[%A-Za-z0-9 :/\-.,T_]{2,40}$"
ColumnType = Literal["bigint", "integer", "decimal(18,2)", "double", "varchar", "boolean", "date", "timestamp"]


class ColumnRule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    source: str = Field(alias="from", pattern=IDENTIFIER)
    target: str = Field(alias="as", pattern=IDENTIFIER)
    type: ColumnType
    # Cột chuỗi chứa ngày ghi nhiều kiểu (2026-01-05, 05/01/2026...): thử lần lượt; không khai báo thì ép kiểu nghiêm.
    date_formats: list[Annotated[str, Field(pattern=DATE_FORMAT)]] | None = Field(default=None, max_length=5)

    @model_validator(mode="after")
    def _formats_only_for_dates(self) -> "ColumnRule":
        if self.date_formats and self.type not in ("date", "timestamp"):
            raise ValueError(f"cột '{self.target}': date_formats chỉ dùng cho kiểu date / timestamp")
        if any("%" not in fmt for fmt in self.date_formats or []):
            raise ValueError(f"cột '{self.target}': mỗi định dạng ngày phải có chỉ thị strptime như %Y, %m, %d")
        return self


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    table: Literal["sales_raw.orders"] = "sales_raw.orders"
    columns: list[ColumnRule] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_mapping(self):
        if len({col.target for col in self.columns}) != len(self.columns):
            raise ValueError("Tên cột đầu ra phải duy nhất.")
        if not self.title.strip():
            raise ValueError("Tên run không được để trống.")
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
