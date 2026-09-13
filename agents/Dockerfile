# Single image for every MINAS agent. Build context is the repo root:
#   docker compose build      (compose sets working dir + command per service)
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app/agents
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the RAG embedding model into the image at build time (cached
# as a Docker layer) so containers never need network access at runtime to
# fetch it — same "no external API dependency at runtime" posture as Ollama.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

COPY agents/ ./agents/
