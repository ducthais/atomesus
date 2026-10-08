# Image Python dùng chung cho backend, Dagster và mock API (deploy/compose.yml). Build được trên amd64 và arm64
# (VM Ampere của Oracle Cloud). Chạy bằng user không phải root; dữ liệu nằm ở /app/data (volume).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Thư viện cài trước code để lần deploy chỉ đổi code không phải cài lại (cache theo file requirements).
COPY requirements.txt ./
COPY requirements ./requirements
RUN pip install -r requirements/dagster.txt

RUN useradd --create-home --uid 1000 app
COPY --chown=app:app . .
RUN mkdir -p /app/data && chown app:app /app/data

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
