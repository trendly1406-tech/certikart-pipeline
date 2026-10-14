# Docker Deployment

## Runtime boundary

The pipeline is a batch/job image, not an HTTP server. An external scheduler such as a Kubernetes `CronJob`, ECS scheduled task, Cloud Run job, or CI operator starts the image with a job command. `doctor` is the safe default command until collection jobs are implemented.

Development and production intentionally use different database ownership:

| Environment | PostgreSQL owner | Compose file |
|---|---|---|
| Local development | `pgvector/pgvector:pg18` container with a named volume | `compose.dev.yml` |
| Production | Managed/external PostgreSQL with backups, HA, encryption, and monitoring | `compose.prod.yml` |

There is no PostgreSQL service in `compose.prod.yml`.

## Local development

```bash
cp .env.example .env
make dev-up
make doctor
```

`make dev-up` waits for `pg_isready`. The database survives `make dev-down`; deleting the named volume is an explicit operator action and is not included in the Makefile.

To test the containerized command against the development database:

```bash
docker compose -f compose.dev.yml --profile tools run --rm pipeline doctor
```

## Image build

```bash
make docker-build
make docker-doctor
```

The multi-stage image:

- pins the uv binary and installs from `uv.lock` with `uv sync --locked`;
- excludes development dependencies and does not ship uv/build tools in the runtime stage;
- runs as numeric UID/GID `10001`, not root;
- copies only the virtual environment and `src/` into the runtime image;
- disables Python bytecode writes and emits unbuffered logs.

Build and scan the image in CI, publish it under an immutable tag/digest, generate an SBOM, and sign it before promotion.

## Production

Inject secrets from the deployment platform; never commit or bake them into the image:

```bash
export CERTIKART_PIPELINE_IMAGE='ghcr.io/acme/certikart-pipeline@sha256:...'
export CERTIKART_DATABASE_URL='postgresql+psycopg://user:password@managed-db:5432/certikart?sslmode=require'
docker compose -f compose.prod.yml run --rm pipeline db-upgrade
docker compose -f compose.prod.yml run --rm pipeline doctor
```

`db-upgrade` is the only production startup step that changes the managed schema. The release
image includes `alembic.ini` and the revision files. Normal jobs run with
`CERTIKART_SCHEMA_MANAGEMENT=migrations`, which prevents their shared `init_db` compatibility call
from issuing `create_all` or ad-hoc DDL. The first retrieval revision expects the legacy `products`
table to exist. Its migration role needs permission to install the `vector` extension, or the
database administrator must install that extension before the release.

The production service is read-only, drops Linux capabilities, denies privilege escalation, and has only a small temporary filesystem. The placeholder `doctor` command must be replaced by a concrete idempotent job command when those commands exist.

Production requirements outside this repository:

- managed database credentials are least-privilege and rotated;
- TLS certificate verification follows the database provider's instructions;
- schema migrations run through `db-upgrade` as a separate, controlled release step;
- network egress is limited to the database and approved source domains;
- job timeouts, retries, concurrency policy, alerts, and a kill switch are configured;
- database backups and point-in-time restore are tested.
