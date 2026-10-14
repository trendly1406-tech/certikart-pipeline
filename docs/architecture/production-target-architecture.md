# Certikart Pipeline: Production Architecture & System Roadmap 🚀

> **System Positioning Statement**:
> *"A production-oriented e-commerce product ingestion and price-comparison pipeline with deterministic product identity resolution, data-quality gates, retailer-specific extraction, and PostgreSQL persistence with global hardware identity indexing. The architecture is designed to evolve toward distributed crawling, event streaming, semantic matching, and dedicated analytical storage as volume increases."*

---

## 1. Implemented Architecture (Current System State)

The current implementation is structured around a solid, type-safe, and auditable foundation designed for correctness and deterministic clustering.

```mermaid
flowchart TD
    subgraph Ingestion["1. Discovery & Retailer Ingestion"]
        CLI[Scheduler / CLI Discovery] --> Sitemaps[Sitemap & Search Ingestion]
        Sitemaps --> Transports[HTTPX + Rate Limiting & Retry Transport]
        Transports --> Parsers[Retailer Parsers: Amazon, Flipkart, Croma]
    end

    subgraph Quality["2. Multi-Signal Quality Gate"]
        Parsers --> DQ[Data Quality Classifier]
        DQ -->|Accessories Detected| RejectQueue[Isolated Disqualified Queue]
        DQ -->|Sanity Checks Passed| Norm[Domain Normalizers: Laptop & Mobile]
        Norm --> PydanticSchemas[LaptopAttributes / MobileAttributes Schemas]
    end

    subgraph Matching["3. Product Identity Resolution"]
        PydanticSchemas --> IDMatcher[100-Point Deterministic Matcher]
        IDMatcher --> MatchScore{Confidence Score}
        MatchScore -->|>=90 Match / 75-89 Strong| AutoLink[Master Canonical Cluster]
        MatchScore -->|60-74 Review| ReviewQueue[Manual / Secondary Match Candidates]
        MatchScore -->|<60 New| NewCluster[New Product Cluster]
    end

    subgraph Storage["4. Relational Master Storage (PostgreSQL)"]
        AutoLink --> PG_Products[(products - Master Variants)]
        AutoLink --> PG_Listings[(retailer_products - Store Listings)]
        AutoLink --> PG_Offers[(offers - Live Price in Integer Paise)]
        AutoLink --> PG_Identifiers[(product_identifiers - ASIN, MPN, GTIN, EAN)]
    end
```

### What is Active & Verified in Code Today
- **PostgreSQL 5-Table Relational Schema**:
  - `products`: Master hardware product clusters with normalized specifications.
  - `retailer_products`: Store-specific product pages with lifecycle tracking and data quality scores.
  - `offers`: Live commercial state (price in integer paise, MRP, coupon discount, seller, stock, ratings).
  - `product_identifiers`: Normalized external hardware identifiers (`ASIN`, `MPN`, `GTIN`, `EAN`) with unique index `(identifier_type, identifier_value)`.
  - Scheduled crawl telemetry is emitted through structured logs and collection reports; interactive imports use `product_import_runs` for durable progress.
- **Domain Identity Normalizers & Pydantic Schemas**:
  - `LaptopIdentityNormalizer` / `LaptopAttributes`: Processor extraction (M1–M5 Pro/Max, A18 Pro, Intel Core Ultra, AMD Ryzen AI, Snapdragon X, MediaTek), RAM type (Unified Memory, LPDDR5X, DDR5), display panel (Liquid Retina XDR, OLED, 4K UHD), GPU VRAM, backlight, and battery Wh.
  - `MobileIdentityNormalizer` / `MobileAttributes`: Families (iPhone 11–19, Galaxy S20–S26, Redmi, Realme, Poco, Vivo, iQOO), display protection (Ceramic Shield, Gorilla Glass Victus 2), resolution (Super Retina XDR, QHD+, 1.5K, FHD+), camera MP/OIS, battery mAh, and fast charging wattage.
- **Data Quality & Hygiene Layer (`src/quality/`)**:
  - Pattern-based peripheral and accessory detection (cases, covers, sleeves, chargers, wireless mice, keyboards, mats).
  - Category price sanity bands (Laptops: ₹10k–₹10L, Mobiles: ₹2.5k–₹3.5L).
- **100-Point Deterministic Matcher (`src/matching/`)**:
  - Point contributions: Brand (20), Model (25), RAM (15), Storage (15), CPU (10), GTIN/MPN (10), Specs (5).
  - Benchmark Results across 67 ground-truth & adversarial test pairs: **100.00% Precision**, **0.00% False Positive Rate (Zero false merges)**.

---

## 2. Target Production Architecture (Next Scaling Layers)

As product volume grows from thousands to millions of SKUs, the system incrementally adopts distributed and event-driven components without requiring an architectural rewrite:

```mermaid
flowchart TD
    subgraph Scaling_Discovery["Scaling Layer 1: Distributed Crawlers"]
        Temporal[Temporal / Airflow Orchestration]
        Proxies[Residential Proxy Rotation & Anti-Bot Pool]
        Workers[Distributed Scrapy / Playwright Worker Pods]
        Temporal --> Proxies --> Workers
    end

    subgraph Scaling_EventBus["Scaling Layer 2: Event Streaming"]
        Workers -->|Raw Source Events| Kafka[Apache Kafka / Redis Streams]
    end

    subgraph Scaling_Quality["Scaling Layer 3: Streaming Normalization"]
        Kafka --> StreamDQ[Streaming Data Quality & Pydantic Validation]
        StreamDQ --> StreamNorm[Category Normalizer Services]
    end

    subgraph Scaling_Matching["Scaling Layer 4: Two-Stage Hybrid Identity"]
        StreamNorm --> ExactID{Exact MPN / GTIN / ASIN?}
        ExactID -->|YES| FastMatch[Instant Index Lookup]
        ExactID -->|NO| DetScore[100-Pt Deterministic Rule Engine]
        DetScore -->|>=90| FastMatch
        DetScore -->|60-89| VectorAI[pgvector / Qdrant Semantic Similarity]
        VectorAI -->|High Similarity| FastMatch
        VectorAI -->|Ambiguous| OpsUI[Internal Ops Review Dashboard]
    end

    subgraph Scaling_Storage["Scaling Layer 5: Polyglot Persistence"]
        FastMatch --> PG[(PostgreSQL: Master Catalog, Listings, Offers)]
        FastMatch --> TS[(ClickHouse / TimescaleDB: High-Velocity Time Series)]
        FastMatch --> RedisCache[(Redis: Sub-Millisecond Search & Lowest-Price Cache)]
    end
```

---

## 3. Scale Triggers: When to Introduce Each Scaling Layer

| Component | Current Stage | When to Migrate (Scale Trigger) | Why Migrate? |
|---|---|---|---|
| **Message Bus (Kafka / Redis Streams)** | In-memory asyncio / ARQ queues | $> 100,000$ daily scrapes or multi-machine crawl fleet | Decouples scraping speed from DB write latency; provides backpressure. |
| **Analytical Time-Series Storage (ClickHouse / TimescaleDB)** | PostgreSQL live `offers` | $> 10\text{M}$ price history rows | Optimizes analytical aggregations (e.g. 1-year price graphs) and compresses historical data. |
| **Vector Matcher (`pgvector` / `Qdrant`)** | 100-pt Deterministic rules | Unstructured categories (fashion, home, unbranded accessories) | Resolves messy, non-standardized titles where deterministic specs are absent. |
| **Distributed Orchestrator (Temporal)** | Local scheduler / CLI jobs | Distributed multi-node scraping clusters | Guarantees durable execution, retry timers, and worker heartbeat liveness. |
| **Residential Proxy Pool** | Direct HTTPX with politeness delays | Cloud IP rate-limiting from target domains | Bypasses strict anti-bot and CAPTCHA barriers at volume. |

---

## 4. Product Identifier Resolution Strategy

With the addition of the **`product_identifiers`** table, cross-retailer reconciliation follows a clean hierarchy:

```text
               Incoming Raw Store Listing
                           │
                           ▼
             ┌───────────────────────────┐
             │ Exact Identifier Lookup?  │
             │ (MPN, GTIN, EAN, ASIN)    │
             └─────────────┬─────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
       Found in DB                 Not Found
             │                           │
             ▼                           ▼
   [Instant Exact Link]         Deterministic Scoring (100 pts)
                                         │
                         ┌───────────────┼───────────────┐
                         ▼               ▼               ▼
                      ≥ 90 pts       60–89 pts        < 60 pts
                         │               │               │
                         ▼               ▼               ▼
                    [Auto-Link]     [Review Queue]   [New Cluster]
```

This model is mathematically explainable, auditable, and eliminates false-positive cross-retailer mergers.
