FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./requirements.txt
COPY requirements/pipeline.txt ./requirements/pipeline.txt
RUN pip install --no-cache-dir -r requirements/pipeline.txt
RUN useradd --create-home pipeline && mkdir -p /app/data && chown pipeline:pipeline /app/data
COPY src ./src
USER pipeline
EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
