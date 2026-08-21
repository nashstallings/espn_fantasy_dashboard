# Cloud Run image for the dashboard API.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
ENV PYTHONPATH=/app/src

# Cloud Run injects PORT. One worker is right here: the service is IO-bound on
# ESPN and scales out by instance, not by in-process concurrency.
ENV PORT=8080
EXPOSE 8080
CMD exec uvicorn espn_dashboard.main:app --host 0.0.0.0 --port ${PORT} --workers 1
