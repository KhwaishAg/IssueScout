# 0001. Technology stack

Status: accepted (P0.7) · Covers: SRS 4.8, 5.1, 5.2, C-7

## Context
One developer, part time, building a retrieval system that must be measurable and
explainable line by line. A second portfolio project (CareLoop) already uses TypeScript,
Node, Express, PostgreSQL, Prisma, Redis, BullMQ, and Gemini, so this project should show
different skills, not repeat them.

## Decision
- **Python 3.12 + FastAPI** for the webhook gateway; **Celery on RabbitMQ** for work.
- **Qdrant** for vectors, **MongoDB** for state (ADR 0003 for Qdrant).
- **tree-sitter** for parsing, **sentence-transformers** for dense embeddings and reranking,
  an open-weight LLM on **Groq** (Ollama as local fallback).
- **Plain functions** for each retrieval stage: no LangChain or LlamaIndex.
- **uv** with a committed lock file; Ruff, mypy strict, pytest.

## Why
- The ML ecosystem is Python-first; FastAPI gives typed validation and async.
- GitHub requires a webhook answer within 10 s (C-1), so the gateway only validates and
  enqueues. RabbitMQ gives durable queues, acknowledgements, and dead-lettering; Kafka is
  too heavy and a Redis queue is what CareLoop uses.
- A modular monolith in two process roles (api, worker) from one image: models are loaded
  once in the worker, which microservices would duplicate.
- Frameworks hide the stages the evaluation must isolate. Each stage as a plain function can
  be measured and swapped for the ablation table.

## Alternatives rejected
Flask/Django (less typing and async support); Kafka (operational weight); LangChain /
LlamaIndex (opaque stages); a hosted proprietary LLM (cost, and it is Gemini territory).

## Consequences
Everything runs on one 2 vCPU / 8 GB VM without a GPU. Pinned, locked versions mean
benchmark numbers do not drift.