"""Mỗi module chạy được khi được import đầu tiên, trong tiến trình Python mới (không qua src.main).

Test thường nạp src.main trước nên vòng import bị che: commit 305168a tạo vòng `run_report → router → agents →
run_report`, làm `make dagster` (scripts/dagster_local.py) không khởi động được mà bộ test vẫn xanh.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULES = sorted(
    ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
    for path in (ROOT / "src").rglob("*.py")
    if "__pycache__" not in path.parts
)
SCRIPTS = ["scripts/dagster_local.py", "scripts/grow_data.py", "scripts/seed_pipeline.py"]


def _fresh(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)


def test_every_src_module_imports_on_its_own():
    failures = []
    for name in MODULES:
        done = _fresh(f"import {name}")
        if done.returncode != 0:
            failures.append(f"{name}: {done.stderr.strip().splitlines()[-1]}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("script", SCRIPTS)
def test_scripts_load_without_import_cycles(script):
    loader = (
        "import importlib.util as u\n"
        f"spec = u.spec_from_file_location('script_under_test', {script!r})\n"
        "spec.loader.exec_module(u.module_from_spec(spec))\n"
    )
    done = _fresh(loader)
    assert done.returncode == 0, done.stderr[-800:]
