"""Small staging contract using the Spec's from/as/type column mappings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IDENTIFIER = r"^[a-z][a-z0-9_]{0,62}$"
ColumnType = Literal["bigint", "integer", "decimal(18,2)", "double", "varchar", "boolean", "date", "timestamp"]


class ColumnRule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    source: str = Field(alias="from", pattern=IDENTIFIER)
    target: str = Field(alias="as", pattern=IDENTIFIER)
    type: ColumnType


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
    user_id: Literal["minh", "an"]
