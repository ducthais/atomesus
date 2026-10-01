import json

import pytest

from src.policy import load_policy
from src.spec import SPEC_FIXTURES_DIR, Spec, policy_violations

MULTI = "spec_02_sales_multi_source.json"


@pytest.fixture(scope="module")
def policy():
    return load_policy()


def _spec(mutate=None) -> Spec:
    data = json.loads((SPEC_FIXTURES_DIR / MULTI).read_text(encoding="utf-8"))
    if mutate:
        mutate(data)
    return Spec.model_validate(data)


@pytest.mark.parametrize("path", sorted(SPEC_FIXTURES_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_fixtures_follow_policy(policy, path):
    spec = Spec.model_validate_json(path.read_text(encoding="utf-8"))
    assert policy_violations(spec, policy) == []


def test_unknown_domain(policy):
    spec = _spec(lambda d: d.update(domain="finance"))
    assert policy_violations(spec, policy) == ["domain 'finance' không khai báo trong domains.yml"]


def test_source_from_other_domain_is_flagged_for_semantic_gate(policy):
    spec = _spec(lambda d: d["sources"][1].update(source_ref="mkt_api"))
    assert any("marketing" in v and "semantic" in v for v in policy_violations(spec, policy))


def test_undeclared_source(policy):
    spec = _spec(lambda d: d["sources"][0].update(source_ref="ghost_pg"))
    assert any("ghost_pg" in v for v in policy_violations(spec, policy))


def test_kind_must_match_declared_source(policy):
    def to_postgres(d):
        d["sources"][1] = {
            k: v for k, v in d["sources"][1].items() if k not in ("endpoint", "records_path", "pagination")
        }
        d["sources"][1].update(kind="postgres", table="payments")

    assert any("kind" in v for v in policy_violations(_spec(to_postgres), policy))


def test_max_tables_joined_is_read_from_budgets(policy):
    rules = policy.budgets.rules.model_copy(update={"max_tables_joined": 1})
    tight = policy.model_copy(update={"budgets": policy.budgets.model_copy(update={"rules": rules})})

    assert policy_violations(_spec(), policy) == []
    assert policy_violations(_spec(), tight) == ["2 nguồn vượt budgets.yml rules.max_tables_joined = 1"]


def test_currency_must_convert_to_domain_base(policy):
    spec = _spec(lambda d: d["sources"][0]["staging"]["columns"][4]["currency"].update(to="USD"))
    assert any("currency.to 'USD'" in v for v in policy_violations(spec, policy))
