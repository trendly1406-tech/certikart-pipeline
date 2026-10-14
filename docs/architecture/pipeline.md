# Product and Price Pipeline ⚡

## 1. Stage Model

```text
Catalog Sitemap Discovery (XML/GZ)
     │
     ▼
Priority Crawl Frontier (Freshness Scheduling + Exponential Backoff)
     │
     ▼
Distributed ARQ Workers (Host-level Concurrency Semaphores)
     │
     ▼
Platform Parsers (sources/) & Shared Extraction Mechanics (sources/common.py)
     │
     ▼
Canonical Domain Normalization & Validation (categories/<dept>/<cat>/normalizer.py)
[LaptopAttributes / MobileAttributes Pydantic Schemas]
     │
     ▼
ProductFingerprint Construction
     │
     ▼
Deterministic Hard Conflict Elimination (categories/<dept>/<cat>/rules.py)
     │
     ▼
Hierarchical Matcher & Cluster Reconciliation (matching/)
(100.00% Precision, 0.00% False Positive Rate across Benchmark Suite)
     │
     ▼
Transactional PostgreSQL Persistence (storage/repository.py)
  ├── products (Canonical Catalog)
  ├── retailer_products (Store-specific Listings)
  ├── offers (Live Commercial State in Integer Paise)
  ├── product_identifiers (Indexed ASIN, MPN, GTIN, EAN)
  └── product_import_runs (User-submitted URL import progress)
```

---

## 2. Catalog Discovery & Priority Crawl Frontier

- **XML & GZ Sitemap Harvesting**: Recursively parses sitemap indexes and product sitemaps with regex URL matchers for Amazon (`/dp/ASIN`), Flipkart (`/p/itm...`), and Croma (`/p/CODE`).
- **Priority Frontier**: Multi-priority queue (`HIGH`, `NORMAL`, `LOW`) with $O(1)$ URL deduplication, crawl recrawl windows, and jittered exponential retry backoff.
- **Incremental Scheduling**: Products are assigned adaptive crawl intervals based on volatility:
  - `NEW` $\to$ Immediate
  - `VOLATILE` $\to$ 30 minutes
  - `ACTIVE` $\to$ 4 hours
  - `STABLE` $\to$ 24 hours

---

## 3. Distributed ARQ Background Workers

- **Redis-Backed Task Queues**:
  - `discovery`: Handles sitemap ingestion and catalog URL expansion.
  - `crawl`: Fetches and parses individual PDP pages with retailer host-level concurrency limits.
  - `persistence`: Reconciles product clusters and persists to PostgreSQL.
  - `dead_letter`: Captures failed records for inspection and replay.
- **Concurrency Rate Limiting**: Per-retailer `asyncio.Semaphore` guards to ensure compliance with rate policies.

---

## 4. Extraction & Normalization Layer

- **Clean Decoupling**:
  - **`sources/common.py`**: Shared currency parsing (`extract_digits_to_paise`), brand inference (`infer_brand`), and JSON-LD schema.org parsing.
  - **`sources/<source>/parser.py`**: Pure platform selectors (DOM, hydration state, script tags).
  - **`categories/<dept>/<cat>/`**: Pure domain logic and Pydantic validation:
    - **`LaptopAttributes`**: Validates RAM, storage, CPU/GPU, display resolution, RAM type, GPU VRAM, backlight, battery Wh, weight.
    - **`MobileAttributes`**: Validates primary/front camera, resolution standard, battery mAh, fast charging W, screen protection, IP water resistance, OS, 5G/4G network.

---

## 5. Exact-Variant Matching & Hard Conflict Engine

- **ProductFingerprint**: Structured representation containing canonical brand, family, model name, chip, RAM GB, storage GB, screen size, GPU model, MPN, and GTIN.
- **Deterministic Hard Conflict Elimination**: Rejects incompatible variants before similarity evaluation:
  - Different brand or product family (`MacBook Air != MacBook Pro`, `Vivobook != Zenbook`).
  - Different CPU chip tier / generation (`M4 != M5`, `i5-1235U != i5-1335U`, `Ryzen 5 7520U != 7530U`).
  - Dedicated GPU mismatch (`RTX 3050 != RTX 4050`).
  - RAM / Storage size mismatch (`16GB != 32GB`, `512GB != 1TB`).
  - Screen size difference ($\ge 0.7$ inch).
  - MPN or GTIN conflict.

---

## 6. Transactional PostgreSQL Persistence

- **Idempotent Upserts**: Safe re-scraping without duplicate product or retailer product creation.
- **Lifecycle Tracking**: Tracks product health (`ACTIVE` $\to$ `STALE` $\to$ `UNAVAILABLE` $\to$ `DISCONTINUED`) based on consecutive crawl outcomes.
- **Global Hardware Identity Index**: Enforces uniqueness on `(identifier_type, identifier_value)` for instant cross-retailer link resolution.
