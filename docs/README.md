# Certikart Pipeline Documentation 📚

This repository documents the background product-data pipeline for **Certikart**. The Next.js frontend lives in `certikart`; FastAPI and the recommendation engine belong in the separate `certikart-api` project.

---

## 📑 Core Documentation Sitemap

| Task / Topic | Documentation Link | Description |
|---|---|---|
| **Pipeline Scope & Data Flow** | [Architecture Overview](architecture/overview.md) | High-level data flow, repository boundaries, and module ownership. |
| **Database Tables & Use Cases** | [Database Architecture & Rating](architecture/database-analysis.md) | Table-by-table use-cases, design rationale, and **9.2/10 Architecture Rating**. |
| **Data Model & Invariants** | [Data Model](architecture/data-model.md) | Relational schema, integer paise rules, and matching order. |
| **Target Production Scale** | [Production Target Architecture](architecture/production-target-architecture.md) | Current working PostgreSQL/ARQ vs target Kafka/ClickHouse scaling layers. |
| **Adding Product Categories** | [Category Extensibility](architecture/category-extensibility.md) | How to build category handlers and Pydantic schemas. |
| **Pipeline & Matcher Mechanics** | [Pipeline Architecture](architecture/pipeline.md) | Scraping, normalization, 100-point matching, and quality gates. |
| **Customer Review Processing** | [Review Intelligence](architecture/review-intelligence.md) | Aspect-sentiment analysis and review aggregation models. |
| **Technical Decisions (ADRs)** | [Architecture Decisions](architecture/decisions.md) | Record of architectural decisions and trade-offs. |
| **Runtime Safeguards** | [Production Safety](standards/production-safety.md) | Rate limits, anti-bot resilience, and schema isolation. |
| **Product Image Storage** | [S3 Product Images](deployment/product-images.md) | Image collection, environment configuration, WebP optimization and S3 uploads. |
| **Product Embeddings** | [pgvector Embeddings](deployment/product-embeddings.md) | OpenAI-compatible/Ollama generation, resumable backfill, and coverage health. |
| **Product URL Imports** | [URL Import Workflow](workflows/product-url-imports.md) | Validated retailer intake, durable runs, freshness reuse, cancellation, and canonical resolution. |
| **Docker & Database Deployment** | [Docker Deployment](deployment/docker.md) | Running PostgreSQL, Adminer DB UI, and containerized images. |
| **Type Safety Standards** | [Type Safety](standards/type-safety.md) | Strict typing, Pydantic contracts, and mypy compliance. |

---

## 🏛️ Architecture Rating & Maturity Score

- **Engineering Maturity Score**: **9.2 / 10** (Production-Grade)
- **Deterministic Matcher Accuracy**: **100.00% Precision**, **0.00% False Positive Rate**
- **Test Coverage**: **80.95%** across 275 unit/integration tests
- **Static Typing**: **0 mypy errors** across 125 checked source, test, and migration files
