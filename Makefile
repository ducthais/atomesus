.PHONY: run dagster test lint format typecheck check clean

run:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Dagster UI + daemon cho các pipeline đã duyệt (cần requirements/dagster.txt)
dagster:
	python scripts/dagster_local.py

test:
	pytest tests/ -v

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

typecheck:
	mypy src/

check: lint format test

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
