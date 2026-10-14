# Product URL imports

Product URL imports are pipeline-owned catalog jobs. The FastAPI backend validates the same public
contract and enqueues work, but it never downloads retailer HTML or writes catalog products.

## Supported input

The first slice accepts exact HTTPS product URLs for laptop and mobile imports from:

- Amazon India: `/dp/<ASIN>` and `/gp/product/<ASIN>`;
- Flipkart: a product path containing `/p/itm...`;
- Croma: a product path ending in `/p/<numeric product ID>`.

Search, category, shortened, credential-bearing, nonstandard-port, and unknown-host URLs are
rejected. Tracking query parameters and fragments are removed before the run is queued. Both the
backend and pipeline validate the URL; the pipeline remains authoritative at the worker boundary.

## Durable flow

```text
POST /api/v1/product-imports
  -> backend product_import_requests
  -> ARQ task_import_product_url
  -> pipeline product_import_runs
  -> complete fresh multi-source reuse OR fetch -> parse
  -> exact-identity search on the other supported retailers
  -> fetch bounded candidates -> exact-variant match -> reconcile -> persist
  -> canonical_product_id
  -> backend status/SSE projection
  -> frontend /products/<canonical_product_id>
```

`product_import_runs` stores the durable state. Redis events are transient progress hints and can
be lost without losing the run. The backend status and SSE routes project the table through the
read-only catalog connection, so a browser reconnect can recover the current stage.

The default freshness window is four hours. A known listing only resolves immediately when its
canonical product already has more than one retailer source. A fresh single-source product still
runs collection and cross-retailer enrichment, so an earlier one-store import does not permanently
skip offer discovery. The advisor URL composer requests a new run when a shopper submits a retailer
URL; multi-source catalog records can still reuse their fresh listing in the worker.

After parsing the supplied listing, the worker builds a compact identity query. It prefers MPN,
then GTIN, then a part/model code from the title, and finally the normalized model name. Amazon,
Flipkart, and Croma searches are bounded to five candidates per other source. Every candidate is
parsed through its normal retailer adapter and compared with the seed fingerprint. Only a
`MATCH` decision is persisted with the seed; review/no-match variants are excluded. Other-retailer
search or product-page failures are best effort and do not fail the supplied URL import.
Retailer parsers promote MPN and GTIN/EAN values into the typed `ParsedProduct` identifier fields,
so these identifiers participate in exact matching instead of remaining display-only attributes.
Amazon exact-search candidates are ordered with the seed brand first before the five-page-fetch
budget is applied; this avoids unrelated sponsored results consuming the entire candidate budget.

## Worker functions

- `task_import_product_url`: validates the payload, creates or resumes the run, checks freshness,
  invokes the source/parser path, runs bounded cross-retailer discovery, reconciles only confirmed
  exact variants, persists them, and returns the exact canonical product ID.
- `task_cancel_product_import`: marks queued/running durable state as cancelled. The orchestrator
  checks the marker before fetch and before persistence boundaries.

Safe public failure codes are `product_rejected`, `retailer_unavailable`,
`invalid_product_data`, and `pipeline_unavailable`. Detailed internal exceptions are not exposed
to the browser.

## Release and validation

Apply the pipeline migration before workers consume import jobs, and apply the backend migration
before its routes receive requests:

```bash
make db-upgrade
make db-current
```

The code path is covered with mocked retailer HTML and temporary databases. A production release
still needs a controlled live Redis/worker/retailer smoke run, rate/ownership policy, retention
cleanup, and additional redirect/short-link handling.
