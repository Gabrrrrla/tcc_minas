# Image for the core-side enforcement sidecar (upf_shaper.py). Separate from
# minas-agent:local on purpose: it needs iproute2 (tc), and rebuilding it never
# touches the agents' image used by the benchmark.
#   docker compose -f docker-compose.yml -f docker-compose.lab.yml build upf-shaper
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir flask==3.0.3
WORKDIR /app
COPY agents/enforcers/upf_shaper.py .
CMD ["python", "upf_shaper.py"]
