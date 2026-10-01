"""Đối chiếu một Spec với chính sách hiện hành. Trần và tỷ giá đọc từ policy, không hardcode."""

from src.policy.models import Policy
from src.spec.models import Spec


def policy_violations(spec: Spec, policy: Policy) -> list[str]:
    """Danh sách vi phạm chính sách; rỗng nghĩa là Spec hợp lệ theo policy/*.yml."""
    domain = policy.domains.domains.get(spec.domain)
    if domain is None:
        return [f"domain '{spec.domain}' không khai báo trong domains.yml"]

    violations: list[str] = []
    max_tables = policy.budgets.rules.max_tables_joined
    if len(spec.sources) > max_tables:
        violations.append(f"{len(spec.sources)} nguồn vượt budgets.yml rules.max_tables_joined = {max_tables}")

    for source in spec.sources:
        declared = domain.sources.get(source.source_ref)
        if declared is None:
            owner = policy.domains.domain_of_source(source.source_ref)
            where = f"thuộc domain '{owner}' (cổng semantic sẽ chặn)" if owner else "không khai báo trong domains.yml"
            violations.append(f"nguồn '{source.id}': source_ref '{source.source_ref}' {where}")
            continue
        if declared.kind != source.kind:
            violations.append(
                f"nguồn '{source.id}': kind '{source.kind}' khác kind '{declared.kind}' của '{source.source_ref}'"
            )
        for column in source.staging.columns:
            if column.currency and column.currency.to != domain.currency.base:
                violations.append(
                    f"nguồn '{source.id}', cột '{column.as_}': currency.to '{column.currency.to}' "
                    f"khác tiền gốc '{domain.currency.base}' của domain"
                )
    return violations
