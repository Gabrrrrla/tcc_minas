# Single image for every MINAS agent. Build context is the repo root:
#   docker compose build      (compose sets working dir + command per service)
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app/agents

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
