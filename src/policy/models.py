"""Model cho 3 file chính sách (SPEC §8.4). Mọi trần và ngưỡng của hệ thống đọc từ đây (NFR-04)."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SourceKind = Literal["postgres", "rest_api", "filesystem"]

# Trường bắt buộc theo loại nguồn trong domains.yml
SOURCE_REQUIRED_FIELD: dict[str, str] = {"postgres": "schema_", "rest_api": "openapi", "filesystem": "root"}


class _Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# ---------- domains.yml ----------


class Currency(_Policy):
    base: str = Field(min_length=3, max_length=3)
    rates: dict[str, float] = Field(min_length=1)

    @model_validator(mode="after")
    def _base_rate_is_one(self) -> "Currency":
        if any(rate <= 0 for rate in self.rates.values()):
            raise ValueError("tỷ giá phải > 0")
        if self.rates.get(self.base) != 1:
            raise ValueError(f"rates phải có {self.base}: 1 (đồng tiền gốc)")
        return self


class SourceDecl(_Policy):
    kind: SourceKind
    connection_ref: str = Field(min_length=1)
    schema_: str | None = Field(default=None, alias="schema")
    openapi: str | None = None
    root: str | None = None
    tables: list[str] | None = None

    @model_validator(mode="after")
    def _kind_fields(self) -> "SourceDecl":
        required = SOURCE_REQUIRED_FIELD[self.kind]
        if getattr(self, required) is None:
            raise ValueError(f"nguồn kind={self.kind} bắt buộc có '{required.rstrip('_')}'")
        return self


class Entity(_Policy):
    canonical: bool
    definition: str = Field(min_length=1)
    tables: list[str] = Field(min_length=1)
    # Cách người dùng gọi thực thể trong yêu cầu (ví dụ "khách hàng"); cổng semantic dò theo tên và các bí danh này.
    aliases: list[str] = Field(default_factory=list)


class Domain(_Policy):
    label: str | None = None
    owner_id: str | None = None
    datasets: list[str] = Field(min_length=1)
    steward: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")
    currency: Currency
    sources: dict[str, SourceDecl] = Field(min_length=1)
    entities: dict[str, Entity] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _entity_tables_use_own_sources(self) -> "Domain":
        for name, entity in self.entities.items():
            for ref in entity.tables:
                source_id, _, obj = ref.partition(".")
                if not obj or source_id not in self.sources:
                    raise ValueError(f"entity '{name}': '{ref}' phải có dạng <source_id>.<tên> với source của domain")
        return self


class UserDefaults(_Policy):
    currency: Currency = Field(default_factory=lambda: Currency(base="VND", rates={"VND": 1, "USD": 25400}))


class Domains(_Policy):
    domains: dict[str, Domain] = Field(min_length=1)
    user_defaults: UserDefaults = Field(default_factory=UserDefaults)
    conformed_dimensions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _source_ids_unique(self) -> "Domains":
        seen: dict[str, str] = {}
        for domain_name, domain in self.domains.items():
            for source_id in domain.sources:
                if source_id in seen:
                    raise ValueError(f"source '{source_id}' khai báo ở cả '{seen[source_id]}' và '{domain_name}'")
                seen[source_id] = domain_name
        entities = {e for d in self.domains.values() for e in d.entities}
        unknown = set(self.conformed_dimensions) - entities
        if unknown:
            raise ValueError(f"conformed_dimensions có thực thể không tồn tại: {sorted(unknown)}")
        return self

    def domain_of_source(self, source_id: str) -> str | None:
        return next((name for name, d in self.domains.items() if source_id in d.sources), None)


# ---------- budgets.yml ----------


class LlmPrice(_Policy):
    input_usd_per_mtok: float = Field(ge=0)
    output_usd_per_mtok: float = Field(ge=0)


class Pricing(_Policy):
    usd_per_tb: float = Field(gt=0)
    llm: dict[str, LlmPrice] = Field(default_factory=dict)  # tên model → giá cho 1 triệu token


class PerRun(_Policy):
    max_bytes_scanned: int = Field(gt=0)
    max_usd: float = Field(gt=0)
    max_llm_calls: int = Field(gt=0)
    max_chat_calls: int = Field(ge=0)
    max_spec_attempts: int = Field(ge=0)
    max_sql_attempts: int = Field(ge=0)
    phase_timeout_sec: int = Field(gt=0)


class PerDay(_Policy):
    max_usd: float = Field(gt=0)


class Rules(_Policy):
    require_partition_filter_on: list[str] = Field(default_factory=list)
    forbid_select_star_in: list[Literal["bronze", "staging", "gold"]] = Field(default_factory=list)
    max_tables_joined: int = Field(ge=1)


class Budgets(_Policy):
    pricing: Pricing
    per_run: PerRun
    per_day: PerDay
    rules: Rules

    @field_validator("rules", mode="before")
    @classmethod
    def _merge_rule_list(cls, value: Any) -> Any:
        """budgets.yml viết rules dạng list các dict một khóa; gộp lại, cấm khóa lặp."""
        if not isinstance(value, list):
            return value
        merged: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, dict) or len(item) != 1:
                raise ValueError("mỗi phần tử rules phải là dict đúng một khóa")
            key, rule = next(iter(item.items()))
            if key in merged:
                raise ValueError(f"rule '{key}' khai báo hai lần")
            merged[key] = rule
        return merged

    @model_validator(mode="after")
    def _day_covers_run(self) -> "Budgets":
        if self.per_day.max_usd < self.per_run.max_usd:
            raise ValueError("per_day.max_usd phải >= per_run.max_usd")
        return self


# ---------- objectives.yml ----------


class Hard(_Policy):
    freshness_minutes: int = Field(gt=0)


class Soft(_Policy):
    cost_usd_per_day: float = Field(ge=0)


class Quality(_Policy):
    max_reject_pct: float = Field(ge=0, le=100)


class Regression(_Policy):
    window_runs: int = Field(ge=1)
    runtime_increase_pct: float = Field(gt=0)
    cost_increase_pct: float = Field(gt=0)
    test_fail_rate_pct: float = Field(ge=0, le=100)
    cooldown_hours: float = Field(ge=0)


class DomainObjectives(_Policy):
    hard: Hard
    soft: Soft
    prefer: Literal["cheapest_feasible"]
    degrade_order: list[Literal["freshness", "completeness"]] = Field(min_length=1)
    quality: Quality
    regression: Regression


# ---------- toàn bộ chính sách ----------


class Policy(_Policy):
    domains: Domains
    budgets: Budgets
    objectives: dict[str, DomainObjectives]

    @model_validator(mode="after")
    def _cross_file(self) -> "Policy":
        declared = set(self.domains.domains)
        covered = {name for name in self.objectives if not name.startswith("_")}
        if declared - covered:
            raise ValueError(f"objectives.yml thiếu domain: {sorted(declared - covered)}")
        if covered - declared:
            raise ValueError(f"objectives.yml có domain không khai báo trong domains.yml: {sorted(covered - declared)}")
        for ref in self.budgets.rules.require_partition_filter_on:
            source_id, _, obj = ref.partition(".")
            if not obj or self.domains.domain_of_source(source_id) is None:
                raise ValueError(f"budgets.yml require_partition_filter_on: '{ref}' trỏ tới nguồn không tồn tại")
        return self
