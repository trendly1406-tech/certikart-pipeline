# Data Model, Invariants & Table Design 📊

## 1. Core Identity Graph

```text
category
  -> product (canonical cluster)
       -> product_identifiers (ASIN, MPN, GTIN, EAN)
       -> retailer_product (store listing)
            -> offer (live terms in integer paise)
```

---

## 2. PostgreSQL Relational Tables & Use-Cases

### Catalog & Identity Layer
- **`products`**: Master canonical catalog entries. Stores exact physical hardware attributes (RAM, storage, CPU, GPU, display, color) validated by category Pydantic schemas.
- **`product_identifiers`**: First-class indexed hardware identifiers (`ASIN`, `MPN`, `GTIN`, `EAN`). Enforces global uniqueness and powers $O(1)$ fast reconciliation.
- **`retailer_products`**: Specific store listing on Amazon, Flipkart, or Croma. Retains raw scraped titles, quality audit score, quality flags, and crawl timestamps.

### Commercial & Telemetry Layer
- **`offers`**: Live commercial terms for each store listing (`price_paise`, `mrp_paise`, `coupon_price_paise`, `in_stock`, `seller`, `rating`).
- **`product_import_runs`**: Durable status and progress for user-submitted product URL imports. Scheduled collection diagnostics are emitted through structured logs and collection reports instead of an unused per-run database table.

### Semantic Retrieval Layer
- **`product_documents`**: Stable, versioned projection of canonical decision fields. It excludes volatile offers and raw retailer HTML.
- **`product_embeddings`**: Provider/model-specific pgvector value tied to the exact document content hash and canonical product ID.

The managed Alembic chain creates the complete catalog on a fresh database and adopts compatible tables created by earlier development bootstrap. Production runs schema changes through `db-upgrade`; normal workers use migration mode and do not execute DDL.

---

## 3. Mandatory Invariants

1. **Integer Paise**: Monetary columns use non-negative `BIGINT` integer paise (`₹1,299.00` = `129900` paise).
2. **UTC Timestamps**: Stored timestamps use `TIMESTAMPTZ` in UTC.
3. **Idempotency**: Scraping and reconciliation runs are idempotent by `(source, source_product_id)`.
4. **Hard Conflict Elimination**: Never merge variants solely from title similarity. Differences in RAM, Storage, CPU generation, or Screen Size trigger immediate conflict rejection.
5. **Schema Validation**: Every category's attributes are validated by Pydantic models (`MobileAttributes`, `LaptopAttributes`) before persistence.

---

## 4. Product Matching Order

1. **Exact Hardware Identifier Match**: MPN, GTIN, EAN, or ASIN lookup via `product_identifiers`.
2. **Deterministic Fingerprint Reconciliation**: 100-point scoring algorithm with brand, family, RAM, storage, screen size, and CPU comparison.
3. **Hard Conflict Gate**: Immediate rejection if any identity-critical attribute mismatches.
4. **Human Review Queue**: Candidates scoring between 60.0 and 85.0 confidence are routed to the review queue without auto-merging.
