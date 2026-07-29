# n-quark
## Architecture

---

# High-Level Architecture

Internet Sources
        │
        ▼
Ingestion Layer
        │
        ▼
Observation Engine
        │
        ▼
Knowledge Graph
        │
        ▼
Deterministic Analytics
        │
        ▼
Market Feature Store
        │
        ▼
Intelligence Engines
        │
        ▼
API Gateway
        │
        ▼
Consumers

---

# Services

crawl-service

Collects websites, event pages and metadata.

---

media-service

Downloads and analyses creatives.

OCR

Embeddings

Logo detection

---

signal-service

Normalizes external APIs.

Spotify

Instagram

YouTube

Google Trends

etc.

---

observation-service

Stores immutable observations.

No updates.

Append only.

---

entity-service

Canonicalizes entities.

Deduplicates artists, venues and organizers.

---

graph-service

Maintains the knowledge graph.

---

analytics-service

Computes deterministic metrics.

Examples:

- organizer diversity
- historical attendance
- venue utilization
- pricing
- recurrence

---

feature-service

Produces versioned ML-ready features.

---

intelligence-service

Runs AI reasoning.

Summaries

Recommendations

Forecasts

Opportunity detection

---

api-gateway

Public API.

Authentication.

Rate limiting.

Aggregation.

---

# Databases

PostgreSQL

Primary relational database.

Neo4j

Knowledge graph.

Redis

Caching and queues.

Qdrant (or pgvector)

Vector search.

MinIO / S3

Raw files and media.

---

# Event Flow

Source

↓

Observation

↓

Entity Resolution

↓

Knowledge Graph

↓

Analytics

↓

Feature Store

↓

Intelligence

↓

API

---

# Technology

Backend

Python

FastAPI

SQLAlchemy

Frontend

React

TypeScript

Tailwind

Infrastructure

Docker

Docker Compose

Future Kubernetes

---

# Design Principles

Service-oriented.

Event-driven.

Append-only observations.

Independent databases.

Version everything.

Deterministic before AI.
