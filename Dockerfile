# Phase 7: one shared image for the whole fleet (world_server.py, agent.py
# --side a, agent.py --side b) - docker-compose.yml picks the command per
# service. Nothing behaves differently than the venv-based local run; this
# just packages the same code the same way the cloud (Phase 8) will run it.
# See docs/PLAN.md §5, docs/DECISIONS.md D32.

FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
