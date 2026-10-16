"""RUNTIME_CONTRACT đọc bằng AST: chỉ nhận đúng một phép gán literal int ở cấp module, không bị ghi lại ở đâu khác."""

import pytest

from src.dagster_runtime import RUNTIME_CONTRACT
from src.dagster_runtime.contract import bundle_contract, current_projects, is_legacy, runtime_contract
from tests.test_dagster_runtime.specs import RENTAL_MODEL, install, rental_spec

V2 = "RUNTIME_CONTRACT = 2\n"


@pytest.mark.parametrize(
    "source,expected",
    [
        (V2, 2),
        ("RUNTIME_CONTRACT = 1\n", 1),
        ("x = 1\n", None),
        ("RUNTIME_CONTRACT = True\n", None),
        ("RUNTIME_CONTRACT = '2'\n", None),
        ("RUNTIME_CONTRACT = 1 + 1\n", None),
        ("RUNTIME_CONTRACT: int = 2\n", None),
        (V2 + "RUNTIME_CONTRACT = 2\n", None),
        (V2 + "if True:\n    RUNTIME_CONTRACT = 1\n", None),
        (V2 + "def f():\n    global RUNTIME_CONTRACT\n    RUNTIME_CONTRACT = 1\n", None),
        (V2 + "def f():\n    RUNTIME_CONTRACT = 1\n", None),
        (V2 + "class C:\n    RUNTIME_CONTRACT = 1\n", None),
        (V2 + "RUNTIME_CONTRACT += 1\n", None),
        (V2 + "for RUNTIME_CONTRACT in [1]:\n    pass\n", None),
        (V2 + "with open('x') as RUNTIME_CONTRACT:\n    pass\n", None),
        (V2 + "(RUNTIME_CONTRACT := 1)\n", None),
        (V2 + "del RUNTIME_CONTRACT\n", None),
        (V2 + "from os import sep as RUNTIME_CONTRACT\n", None),
        (V2 + "def RUNTIME_CONTRACT():\n    pass\n", None),
        (V2 + "def f(RUNTIME_CONTRACT=1):\n    pass\n", None),
        ("RUNTIME_CONTRACT = (\n", None),
    ],
)
def test_runtime_contract_accepts_only_one_literal_int_assignment(source, expected):
    assert runtime_contract(source) == expected


def test_reading_the_contract_never_executes_the_file(tmp_path):
    marker = tmp_path / "executed"
    assert runtime_contract(f"RUNTIME_CONTRACT = 2\nopen({str(marker)!r}, 'w')\n") == 2
    assert not marker.exists()


def test_rendered_bundle_carries_the_current_contract(tmp_path):
    project = install(tmp_path, rental_spec())

    assert bundle_contract(project) == RUNTIME_CONTRACT and not is_legacy(project)
    assert current_projects(tmp_path) == [project]


def test_legacy_and_temporary_bundles_are_not_current(tmp_path):
    project = install(tmp_path, rental_spec())
    old = tmp_path / "fct_old"
    (old / "infra" / "dagster" / "generated").mkdir(parents=True)
    (old / "infra" / "dagster" / "generated" / "fct_old_asset.py").write_text("x = 1\n", encoding="utf-8")
    for name in (f"{RENTAL_MODEL}.tmp-abc", f"{RENTAL_MODEL}.old-abc"):
        (tmp_path / name).mkdir()
    no_asset = tmp_path / "fct_no_asset"
    no_asset.mkdir()

    assert is_legacy(old) and not is_legacy(no_asset)
    assert current_projects(tmp_path) == [project]
    assert current_projects(tmp_path / "missing") == []
