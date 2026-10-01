import shutil

import pytest
import yaml

from src.policy import POLICY_DIR, PolicyError, load_policy


@pytest.fixture
def policy_dir(tmp_path):
    """Bản sao policy/ để sửa trong test mà không đụng file thật."""
    target = tmp_path / "policy"
    shutil.copytree(POLICY_DIR, target)
    return target


def _edit(policy_dir, name, mutate):
    path = policy_dir / f"{name}.yml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_repo_policy_loads_with_spec_values():
    policy = load_policy()

    assert policy.budgets.per_run.max_bytes_scanned == 5_000_000_000
    assert policy.budgets.per_run.max_llm_calls == 6
    assert policy.budgets.rules.max_tables_joined == 6
    assert policy.budgets.rules.require_partition_filter_on == ["sales_pg.orders"]
    assert policy.objectives["sales"].quality.max_reject_pct == 1.0
    assert policy.domains.domains["sales"].sources["sales_pg"].schema_ == "sales_raw"
    assert policy.domains.domain_of_source("mkt_api") == "marketing"


def test_changing_yaml_changes_loaded_value_without_restart(policy_dir):
    _edit(policy_dir, "budgets", lambda d: d["per_run"].update(max_llm_calls=3))

    assert load_policy(policy_dir).budgets.per_run.max_llm_calls == 3


@pytest.mark.parametrize("block", ["quality", "regression"])
def test_rejects_domain_missing_required_objectives_block(policy_dir, block):
    _edit(policy_dir, "objectives", lambda d: d["marketing"].pop(block))

    with pytest.raises(PolicyError, match=f"marketing.*{block}"):
        load_policy(policy_dir)


def test_rejects_domain_without_objectives(policy_dir):
    _edit(policy_dir, "objectives", lambda d: d.pop("marketing"))

    with pytest.raises(PolicyError, match="thiếu domain.*marketing"):
        load_policy(policy_dir)


def test_rejects_objectives_for_undeclared_domain(policy_dir):
    _edit(policy_dir, "objectives", lambda d: d.update(finance=d["sales"]))

    with pytest.raises(PolicyError, match="finance"):
        load_policy(policy_dir)


def test_rejects_partition_rule_on_unknown_source(policy_dir):
    _edit(policy_dir, "budgets", lambda d: d["rules"][0].update(require_partition_filter_on=["ghost_pg.orders"]))

    with pytest.raises(PolicyError, match="ghost_pg.orders"):
        load_policy(policy_dir)


def test_rejects_duplicate_rule(policy_dir):
    _edit(policy_dir, "budgets", lambda d: d["rules"].append({"max_tables_joined": 2}))

    with pytest.raises(PolicyError, match="hai lần"):
        load_policy(policy_dir)


def test_rejects_source_missing_kind_specific_field(policy_dir):
    _edit(policy_dir, "domains", lambda d: d["domains"]["sales"]["sources"]["sales_pg"].pop("schema"))

    with pytest.raises(PolicyError, match="schema"):
        load_policy(policy_dir)


def test_rejects_source_id_declared_in_two_domains(policy_dir):
    def dup(d):
        d["domains"]["marketing"]["sources"]["sales_pg"] = d["domains"]["sales"]["sources"]["sales_pg"]

    _edit(policy_dir, "domains", dup)

    with pytest.raises(PolicyError, match="sales_pg"):
        load_policy(policy_dir)


def test_rejects_entity_table_from_other_domain(policy_dir):
    _edit(policy_dir, "domains", lambda d: d["domains"]["sales"]["entities"]["order"].update(tables=["mkt_api.leads"]))

    with pytest.raises(PolicyError, match="mkt_api.leads"):
        load_policy(policy_dir)


def test_rejects_currency_without_base_rate(policy_dir):
    _edit(policy_dir, "domains", lambda d: d["domains"]["sales"]["currency"].update(rates={"USD": 25400}))

    with pytest.raises(PolicyError, match="VND"):
        load_policy(policy_dir)


def test_rejects_unknown_key(policy_dir):
    _edit(policy_dir, "budgets", lambda d: d["per_run"].update(max_tokens=1000))

    with pytest.raises(PolicyError, match="max_tokens"):
        load_policy(policy_dir)


def test_reports_invalid_yaml_with_file_name(policy_dir):
    (policy_dir / "budgets.yml").write_text("per_run: [unclosed", encoding="utf-8")

    with pytest.raises(PolicyError, match="budgets.yml"):
        load_policy(policy_dir)


def test_reports_missing_file(policy_dir):
    (policy_dir / "objectives.yml").unlink()

    with pytest.raises(PolicyError, match="objectives.yml"):
        load_policy(policy_dir)
