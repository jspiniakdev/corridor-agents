# Phase 7: one shared image for the whole fleet (world_server.py, agent.py
# --side a, agent.py --side b) - docker-compose.yml picks the command per
# service. Nothing behaves differently than the venv-based local run; this
# just packages the same code the same way the cloud (Phase 8) will run it.
# See docs/PLAN.md §5, docs/DECISIONS.md D32.

FROM python:3.13-slim

# Unbuffered stdout/stderr: Cloud Run captures logs line-by-line as they're
# written, and Python otherwise block-buffers when stdout isn't a TTY - so a
# crashing container's last (most useful) prints never make it to Cloud
# Logging. Cost us a whole debugging session on the D41 token-expiry crash.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
