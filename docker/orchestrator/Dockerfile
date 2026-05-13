# Оркестратор + анализатор (Python).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /work
COPY orchestrator/pyproject.toml orchestrator/pyproject.toml
COPY orchestrator/src orchestrator/src
COPY analyzer/pyproject.toml analyzer/pyproject.toml
COPY analyzer/src analyzer/src

RUN pip install --no-cache-dir ./orchestrator ./analyzer

WORKDIR /work
ENTRYPOINT ["bas-orchestrator"]
CMD ["baseline_wifi"]
