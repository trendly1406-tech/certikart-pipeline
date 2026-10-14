# Certikart Pipeline 🚀

Background data pipeline for **Certikart**. It discovers, collects, normalizes, and reconciles product listings across Indian e-commerce retailers (**Amazon**, **Flipkart**, **Croma**); resolves exact variants using deterministic hard-conflict elimination; validates data quality and accessory contamination; and publishes trustworthy canonical records to **PostgreSQL** with distributed ARQ background workers.

> **Note**: This repository owns the data pipeline. Authentication, questionnaires, recommendations, feedback, and public consumer APIs belong to the separate `certikart-api` service.

---

## 🏗️ Architecture & Source Layout

```text
src/
├── categories/          # Hierarchical taxonomy & Pydantic domain plugins
│   ├── contracts.py     # CategoryDefinition, SubcategoryDefinition, AttributeValue
│   ├── handler.py       # CategoryHandler Protocol interface
│   ├── registry.py      # Department, category, and handler lookup registry
│   └── electronics/     # Top-Level Category / Department
│       ├── laptop/      # Laptop domain plugin (handler, normalizer, rules, LaptopAttributes schema)
│       └── mobile/      # Smartphone domain plugin (handler, normalizer, rules, MobileAttributes schema)
├── sources/             # Retailer adapters and shared parsing mechanics
│   ├── common.py        # Price extraction (integer paise), brand recognition, JSON-LD decoding
│   ├── amazon/          # Amazon India adapter and DOM parser
│   ├── flipkart/        # Flipkart adapter and DOM/JSON-LD parser
│   └── croma/           # Croma adapter and hydration state parser
├── collectors/          # Sitemap discovery engine, priority crawl frontier, and scheduler policies
├── matching/            # Product fingerprinting, reconciliation, and 100-point hard conflict matcher
│   ├── benchmark_runner.py # Empirical evaluation harness for precision, recall, and F1
│   ├── matcher.py       # Deterministic scoring with hard conflict gates
│   └── reconciliation.py# Multi-store canonical clustering and confidence assignment
├── storage/             # PostgreSQL SQLAlchemy models, engine, and transactional repository
│   ├── models.py        # 5-table relational schema (products, retailer_products, offers, etc.)
│   ├── repository.py    # Idempotent persistence with deduplicated global identity index
│   └── engine.py        # Connection pooling and schema initialization
├── workers/             # Distributed ARQ async worker daemon, queues, and background tasks
└── jobs/                # Scheduled and operator-triggered CLI commands
```

Tests strictly mirror the source modules under `tests/` across 100+ files.

---

## ⚡ Quick Start

```bash
# 1. Clone & install dependencies
cp .env.example .env
make install

# 2. Run quality checks & test suite
make check

# 3. Start local PostgreSQL & Database Web Visualizer
make dev-up
```

---

## 🚀 How to Run the Pipeline

### 1. Start Storage Services
Start the development PostgreSQL database and Web Visualizer container:
```bash
make dev-up
```

### 2. Run End-to-End Bulk Crawl & Reconciliation
Discover, parse, match, and persist live product data across all retailers into PostgreSQL:
```bash
# Collect across all sources (Amazon, Flipkart, Croma) and categories (Laptops, Mobiles)
PYTHONPATH=src uv run python -m jobs.cli bulk-collect --category all --sources all --limit 25 --persist

# Or scrape a specific category (e.g. laptops only):
PYTHONPATH=src uv run python -m jobs.cli bulk-collect --category laptop --sources all --limit 20 --persist
```

### 3. Run the Matcher Accuracy Benchmark
Evaluate deterministic matching precision, recall, F1 score, and false positive rates against ground-truth pairs:
```bash
make benchmark
# Or directly:
PYTHONPATH=src uv run python -m matching.benchmark_runner
```

### 4. Run Distributed ARQ Async Workers
Process async crawling and reconciliation jobs in the background:
```bash
# Process jobs in burst mode (processes existing queue then exits)
PYTHONPATH=src uv run python -m jobs.cli worker --burst

# Check worker queue status
PYTHONPATH=src uv run python -m jobs.cli queue-status
```

---

## 📊 How to Visualize the Pipeline Data & Results

### Option 1: Interactive Web Database UI (`Adminer`)
A database visualizer is automatically provided when running `make dev-up`.

1. Open **[http://localhost:8081](http://localhost:8081)** in your browser (or run `make db-ui`).
2. Login with these connection parameters:
   - **System**: `PostgreSQL`
   - **Server**: `postgres:5432`
   - **Username**: `certikart`
   - **Password**: `certikart`
   - **Database**: `certikart_pipeline`
3. **Key Tables to Inspect**:
   - **`products`**: Master canonical catalog entries with normalized JSON hardware specs.
   - **`retailer_products`**: Specific store listings with `quality_status` (`VALID` vs `SUSPICIOUS`) and `lifecycle_status`.
   - **`offers`**: Real-time prices (`price_paise`, `mrp_paise`, `coupon_price_paise`, `in_stock`, `seller`).
   - **`product_identifiers`**: Global normalized identity index (`ASIN`, `MPN`, `GTIN`, `EAN`).
   - **`product_import_runs`**: Durable status and progress for user-submitted product URL imports.

---

### Option 2: Rich Terminal Visualizer
When running `bulk-collect` or `reconcile-demo`, the CLI prints Rich formatted tables showing:
- **Discovered Products & Multi-Store Clusters**: Highlights cross-store matches between Amazon, Flipkart, and Croma.
- **Price Spread & Savings**: Shows live price differences across retailers for the same physical product.
- **Data Quality & Accessory Gate**: Displays filtered items and suspicious flag breakdowns.

---

### Option 3: JSON Telemetry Reports
Detailed reconciliation and scrape results are saved as structured JSON artifacts:
```bash
# Inspect generated report
cat collection_report.json | jq .summary
```

---

## 🗄️ Relational PostgreSQL Data Model

```
┌──────────────────────┐         ┌─────────────────────────┐
│       products       │◄────────┤    retailer_products    │
│ (Canonical Catalog)  │         │ (Store Listings)        │
└──────────┬───────────┘         └────────────┬────────────┘
           │                                  │
           │ 1:N                              │ 1:1
           ▼                                  ▼
┌──────────────────────┐         ┌─────────────────────────┐
│ product_identifiers  │         │         offers          │
│ (ASIN, MPN, GTIN, EAN)│         │ (Live Price in Paise)   │
└──────────────────────┘         └─────────────────────────┘
```

1. **Integer Paise**: All prices are stored in integer paise (`₹1,299.00` = `129900` paise) to prevent floating-point rounding errors.
2. **UTC Timestamps**: All temporal fields are stored as timezone-aware UTC (`TIMESTAMPTZ`).
3. **Pydantic Validation**: All external boundary data is strictly validated with `LaptopAttributes` and `MobileAttributes` schemas.

---

## 🎯 Benchmark & Engineering Standards

```text
============================= 229 passed in 20.51s =============================
Required test coverage of 80% reached. Total coverage: 82.88%
0 mypy errors across 100 source files
0 ruff errors across all files
Deterministic Matcher: 100.00% Precision, 0.00% False Positive Rate
```

---

## 🛠️ Makefile Commands Reference

| Command | Description |
|---|---|
| `make dev-up` | Start PostgreSQL container and DB Visualizer Web UI (`http://localhost:8081`). |
| `make db-ui` | Open the database visualizer Web UI in your default browser. |
| `make dev-down` | Stop development services without deleting PostgreSQL data. |
| `make bulk-collect` | Discover, scrape, and reconcile products across all categories and sources. |
| `make benchmark` | Run empirical accuracy & benchmark evaluation on deterministic matcher. |
| `make check` | Run formatting check, linter (`ruff`), type checker (`mypy`), and test suite (`pytest`). |
| `make test` | Run complete unit and integration test suite with coverage enforcement. |
| `make doctor` | Inspect system configuration and diagnostic health report. |
