# Image cho Hugging Face Space (Docker SDK, CPU Basic miễn phí: 2 vCPU, 16 GB RAM), xem docs/deploy-free.md.
# Workflow "Deploy Space" chép file này thành Dockerfile ở gốc repo Space. Space chạy container bằng uid 1000.
FROM caddy:2 AS caddy

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production

WORKDIR /app

COPY requirements.txt ./
COPY requirements ./requirements
RUN pip install -r requirements/dagster.txt huggingface_hub

COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
RUN useradd --create-home --uid 1000 app
COPY --chown=app:app . .
RUN mkdir -p /app/data && chown app:app /app/data

USER app
ENV HOME=/home/app \
    XDG_CONFIG_HOME=/home/app/.config \
    XDG_DATA_HOME=/home/app/.local/share
EXPOSE 7860

CMD ["bash", "deploy/hf-space/start.sh"]
