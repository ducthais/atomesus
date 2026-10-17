.PHONY: run dagster mock-api test lint format typecheck check eval clean

run:
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Mock REST API làm nguồn rest_api (sales_api, mkt_api), khớp PIPELINE_MOCK_API_URL mặc định
mock-api:
	uvicorn infra.mock_api.app:app --host 127.0.0.1 --port 8100

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

# Benchmark agent 20 ca bằng LLM thật (cần Postgres nguồn, `make mock-api` và OPENAI_API_KEY); ghi eval/results/latest.md
eval:
	python eval/run_eval.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
