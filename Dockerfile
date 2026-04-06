FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NUDGE_TIMEZONE=Asia/Kolkata

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY dashboard ./dashboard
COPY data ./data
COPY README.md .

ENV NUDGE_DATA_DIR=/app/data \
    NUDGE_DASHBOARD_HOST=0.0.0.0 \
    OLLAMA_BASE_URL=http://host.docker.internal:11434

CMD ["python", "-m", "app.dashboard"]
