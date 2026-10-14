# Product embeddings and pgvector backfill

Certikart stores semantic product documents and their vectors in the same PostgreSQL catalog as canonical products. The relational `products` row remains authoritative. `product_documents` is a stable text projection, and `product_embeddings` contains provider/model-specific vectors for that projection.

Embedding generation and semantic search are independent feature flags. Generation is enabled in the current local development environment for the explicit backfill workflow; ordinary collection still updates canonical documents without calling an external embedding provider. Semantic search remains disabled until the vectors are rebuilt and retrieval quality is evaluated.

## Configuration

The pipeline supports OpenAI-compatible `POST /embeddings` providers and Ollama's native
`POST /api/embed` endpoint. Both adapters validate vector count, finite values, and dimensions
before writing anything.

The backend advisor uses OpenRouter for chat and candidate reranking. The pipeline can use the
same OpenRouter API key for embeddings through its OpenAI-compatible `/embeddings` endpoint.
The two applications keep separate environment variable names because they have separate
runtime configuration and deployment boundaries; configure the same secret value in each.

Ollama with BGE-M3 remains an offline option. It produces 1,024-dimensional multilingual
embeddings and does not require another cloud credential:

```bash
ollama pull bge-m3
```

```dotenv
CERTIKART_EMBEDDING_GENERATION_ENABLED=true
CERTIKART_EMBEDDING_API_FORMAT=ollama
CERTIKART_EMBEDDING_PROVIDER=ollama
CERTIKART_EMBEDDING_MODEL=bge-m3
CERTIKART_EMBEDDING_API_KEY=
CERTIKART_EMBEDDING_BASE_URL=http://localhost:11434
CERTIKART_EMBEDDING_DIMENSIONS=1024
CERTIKART_EMBEDDING_BATCH_SIZE=16
CERTIKART_EMBEDDING_TIMEOUT_SECONDS=60
CERTIKART_SEMANTIC_SEARCH_ENABLED=false
```

After Ollama is running, use `embedding-backfill` below. The model is downloaded and the
provider is called only when that explicit command or its worker task runs.

For OpenRouter with Qwen3 Embedding 8B:

```dotenv
CERTIKART_EMBEDDING_GENERATION_ENABLED=true
CERTIKART_EMBEDDING_API_FORMAT=openai
CERTIKART_EMBEDDING_PROVIDER=openrouter
CERTIKART_EMBEDDING_MODEL=qwen/qwen3-embedding-8b
CERTIKART_EMBEDDING_API_KEY=your-openrouter-key
CERTIKART_EMBEDDING_BASE_URL=https://openrouter.ai/api/v1
CERTIKART_EMBEDDING_DIMENSIONS=4096
CERTIKART_EMBEDDING_BATCH_SIZE=16
CERTIKART_EMBEDDING_TIMEOUT_SECONDS=60
CERTIKART_SEMANTIC_SEARCH_ENABLED=false
```

Confirm the returned vector dimension with a single controlled embedding request before starting
a backfill. Existing embeddings are retained under their original provider/model identity; the
new model creates a separate versioned vector set. Do not enable semantic search until vectors
are backfilled and retrieval has been evaluated against labeled shopper queries.

The generic cloud example remains available for other OpenAI-compatible embedding providers:

```dotenv
CERTIKART_EMBEDDING_GENERATION_ENABLED=true
CERTIKART_EMBEDDING_API_FORMAT=openai
CERTIKART_EMBEDDING_PROVIDER=openai
CERTIKART_EMBEDDING_MODEL=text-embedding-3-small
CERTIKART_EMBEDDING_API_KEY=
CERTIKART_EMBEDDING_BASE_URL=https://api.openai.com/v1
CERTIKART_EMBEDDING_DIMENSIONS=1536
CERTIKART_EMBEDDING_BATCH_SIZE=32
CERTIKART_EMBEDDING_TIMEOUT_SECONDS=30

# Keep disabled until hybrid retrieval is implemented and benchmarked.
CERTIKART_SEMANTIC_SEARCH_ENABLED=false
```

For a local OpenAI-compatible endpoint, set a different provider label and base URL. The API key may be empty for endpoints that do not require authentication:

```dotenv
CERTIKART_EMBEDDING_PROVIDER=openai-compatible
CERTIKART_EMBEDDING_MODEL=local-embedding-model
CERTIKART_EMBEDDING_API_KEY=
CERTIKART_EMBEDDING_BASE_URL=http://localhost:11434/v1
```

Never commit a real API key. Supply it through the local ignored `.env`, container secret, or deployment secret manager.

## Operations

The managed revision chain creates the complete catalog on a fresh database and adopts compatible
tables from older bootstrap environments when they already exist. Run the migration as a controlled
release step before starting migration-mode workers or checking embedding coverage:

```bash
make db-upgrade
make db-current
```

In the production Compose model, run the same command against the release image:

```bash
docker compose -f compose.prod.yml run --rm pipeline db-upgrade
```

Production uses `CERTIKART_SCHEMA_MANAGEMENT=migrations`, so normal worker and CLI startup never
runs `create_all` or ad-hoc `ALTER TABLE` statements. Development and tests retain the default
`bootstrap` mode for isolated databases.

Inspect coverage without calling the provider:

```bash
make embedding-status
```

Generate at most 1,000 missing or stale vectors:

```bash
PYTHONPATH=src uv run python -m jobs.cli embedding-backfill --limit 1000
```

Limit a run to one stored category when needed:

```bash
PYTHONPATH=src uv run python -m jobs.cli embedding-backfill --category laptop --limit 500
```

The equivalent ARQ function is `task_backfill_product_embeddings`. It is registered with a 30-minute task timeout but is not automatically scheduled or chained from collection.

Before embedding, the job creates documents missing from older catalog rows and refreshes documents whose projection version changed. It then selects only rows with no embedding, a stale content hash, or the wrong dimension for the configured provider/model. Each successful batch commits separately, so a failed job can resume without rewriting current rows.

The health response reports total products, document gaps, current vectors, missing vectors, stale vectors, coverage percentage, and readiness for that exact provider/model/dimension/category combination. This is coverage health; an approximate-nearest-neighbor PostgreSQL index and semantic recommendation query remain disabled until the later retrieval benchmark phase.

No provider request or catalog backfill runs during setup, application startup, `doctor`, or `embedding-status`.
