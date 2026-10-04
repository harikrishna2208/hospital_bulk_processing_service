# Hospital Bulk Processing Service

A service for validating and processing hospital records uploaded as CSV. It creates each
hospital through the **Hospital Directory API** (`https://hospital-directory.onrender.com`)
and activates the batch only when every hospital is created successfully. The directory API
is a separate downstream service accessed over HTTP.

## Architecture

```
Client
  |
  | POST /hospitals/bulk (CSV)
  v
FastAPI Bulk Service
  |  1. validate CSV (stdlib csv module, max 20 rows)
  |  2. generate batch UUID, register job state, return 202 immediately
  |  3. background task: bounded-concurrency fan-out (asyncio.Semaphore)
  |
  | async HTTP (httpx.AsyncClient, retried w/ backoff on 429/5xx/timeout/connection errors)
  v
Hospital Directory API
  |
  +--> POST /hospitals/                              (create, per row)
  +--> PATCH /hospitals/batch/{batch_id}/activate     (only if ALL rows succeeded)
  +--> DELETE /hospitals/batch/{batch_id}             (cleanup, if any row failed)

Client polls:
  GET /hospitals/bulk/{batch_id}    -> progress + final result
```

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe for this service |
| GET | `/health/dependencies` | Also checks the downstream API's own health (`GET /`) |
| POST | `/hospitals/bulk` | Upload CSV, returns `{batch_id, status, total_hospitals}` immediately (202), processes in the background |
| GET | `/hospitals/bulk/{batch_id}` | Poll progress / final result |
| POST | `/hospitals/bulk/validate` | Validate a CSV's shape only — no downstream calls, no batch created |
| POST | `/hospitals/bulk/{batch_id}/resume` | Re-attempt only the rows still `failed` for an existing batch, without duplicating already-created hospitals |
| DELETE | `/hospitals/bulk/{batch_id}` | Deletes every hospital created under this batch downstream (via the directory API's own batch-delete) and clears local job state; mainly for cleaning up test data |

## Status Values

Batch-level `status` (on `BulkBatchStatusResponse`):

| Value | Meaning |
|---|---|
| `processing` | Background fan-out is still running |
| `completed` | All rows succeeded and the batch was activated |
| `failed` | At least one row failed; the batch was not activated |

Per-row `status` (on each entry in `hospitals`):

| Value | Meaning |
|---|---|
| `processing` | Row is queued/in-flight, not yet resolved |
| `created` | Hospital created downstream, not yet activated (transient — only visible mid-batch before finalization) |
| `created_and_activated` | Hospital created and the batch activation succeeded |
| `failed` | Row's creation failed (terminal 4xx, or retries exhausted on a transient error) |
| `activation_failed` | All rows were created, but the `PATCH .../activate` call itself failed |
| `cleanup_failed` | A previously-created hospital could not be removed during rollback cleanup; it may still exist downstream, inactive |

## CSV Format

```csv
name,address,phone
General Hospital,123 Main St,555-1234
City Hospital,456 Oak St,555-5678
Memorial Hospital,789 Pine St,
```

- `name`, `address` are required and cannot be blank.
- `phone` is optional.
- Header must be exactly `name,address,phone` — missing or extra columns are rejected.
- Maximum 20 hospital data rows per upload.
- Quoting (e.g. commas inside quoted values) is handled via Python's `csv` module.

Uploads are limited to 20 hospital records.

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | |
| `PORT` | `8000` | Port uvicorn binds to |
| `HOSPITAL_DIRECTORY_BASE_URL` | `https://hospital-directory.onrender.com` | Downstream base URL |
| `REQUEST_TIMEOUT_SECONDS` | `10` | Per-request downstream timeout |
| `MAX_RETRIES` | `3` | Max attempts per downstream call (includes the first) |
| `RETRY_BASE_DELAY_SECONDS` | `0.5` | Exponential backoff base delay |
| `MAX_CONCURRENCY` | `5` | Max concurrent downstream hospital-creation calls |
| `MAX_CSV_ROWS` | `20` | Max hospital rows accepted per upload |
| `MAX_UPLOAD_SIZE_BYTES` | `1048576` (1 MiB) | Hard cap on upload size, rejected with 413 before CSV parsing even runs |

## Running Locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Requires Python 3.11+ (developed and tested on 3.12 — `pydantic-core`'s compiled wheels do not
yet support 3.14; use 3.12/3.13 if your system Python is newer).

Swagger UI: http://localhost:8000/docs
OpenAPI spec: http://localhost:8000/openapi.json

## Testing

```bash
pytest
```

45 tests, covering every failure scenario called out in the spec:

- **CSV parsing/validation**: valid parse, empty file, header-only, missing/extra column,
  blank required field, >20 rows, exactly 20 rows, quoted commas, whitespace.
- **Generic retry helper**: first-attempt success, retries-until-success, exhausts after max
  attempts, skips retry for non-retryable errors.
- **Downstream client** (`hospital_directory_client.py`): retry-classification for 429/5xx
  (retryable) vs 400/404/422 (terminal); end-to-end retry-then-succeed for 429, 5xx, timeout,
  and connection-refused; retry exhaustion after `MAX_RETRIES`; 404-on-`get_batch` translated
  to `[]`; 404 propagated as an error from `delete_batch`.
- **Batch orchestration** (`bulk_processing_service.py`): all-succeed → activate; partial
  failure → cleanup + no activation; activation failure → cleanup attempted; transient failure
  → retried then succeeds.
- **API integration tests** (mocked downstream via `respx`, full ASGI stack): successful batches
  are activated; partial failures trigger cleanup without activation; oversized batches are
  rejected; missing batches return 404; CSV validation makes no downstream calls; batch deletion
  removes downstream and local state; oversized uploads are rejected with 413 before parsing.

Lint: `ruff check .` (config in `pyproject.toml`) — zero findings.

## Docker

```bash
docker compose up --build
```

Runs as a non-root user, respects `$PORT`, single service (no extra infrastructure containers).

## Design Decisions

- **Why FastAPI**: async-native, automatic OpenAPI/Swagger docs, Pydantic request/response
  validation, clean dependency injection.
- **Why async HTTP + bounded concurrency, not Kafka**: uploads are capped at 20 hospitals, so
  this is a small, bounded HTTP fan-out/fan-in workload. `httpx.AsyncClient` with a
  shared connection pool and an `asyncio.Semaphore(MAX_CONCURRENCY)` gives sufficient throughput
  without the operational cost of broker/consumer infrastructure. Kafka would become
  appropriate for very large batches, sustained high throughput, multiple independent
  consumers, or durable event streaming — none of which apply here.
- **Why in-memory job state**: the `BatchRepository` is written behind a small interface so it
  could be swapped for Redis/Postgres later. State is lost on restart and is not shared across
  multiple replicas.
- **Retry strategy**: only connection errors, timeouts, HTTP 429, and HTTP 5xx are retried, with
  exponential backoff (`base_delay * 2^(attempt-1)`), up to `MAX_RETRIES` total attempts. 400s
  (and any other 4xx) are treated as terminal, non-retryable row failures — retrying a
  validation/business error would just waste time and risk duplicate side effects.
- **Activation rule**: the batch is activated (`PATCH .../activate`) only when
  `successful_creations == total_hospitals`. Any failure — including the activation call
  itself failing — triggers a best-effort `DELETE .../batch/{batch_id}` cleanup so no orphaned,
  permanently-inactive hospitals are left behind. Cleanup success/failure is recorded
  separately (`cleanup_status`) and never silently converted into `batch_activated: true`.
- **Progress tracking**: `POST /hospitals/bulk` returns immediately (202) with
  `{batch_id, status: "processing", total_hospitals}`; the actual downstream fan-out runs as a
  FastAPI background task. `GET /hospitals/bulk/{batch_id}` exposes live progress and the final
  result.
- **Resume capability**: `POST /hospitals/bulk/{batch_id}/resume` only re-attempts rows whose
  stored status is `failed`, reusing the same batch ID and the original CSV row data retained
  in the batch's in-memory job record. Rows that already succeeded are left untouched, so
  resuming cannot create duplicate hospitals.
- **Idempotency limitation**: the downstream API's OpenAPI contract exposes no idempotency key
  for `POST /hospitals/`. If our own retry of a create call succeeds server-side but the
  response is lost before we see it, a retry could create a duplicate hospital — this is a
  known limitation of the downstream API, not something we can fully work around without
  inventing unsupported server behavior. Kept the retry window as small and conservative as
  practical (non-retried on definite business-logic failures) to minimize exposure.
- **Downstream API behavior**: hospitals created with a `creation_batch_id` start with
  `active: false` and become active after a successful `PATCH .../activate`. The downstream
  `GET /hospitals/batch/{batch_id}` returns HTTP 404 (not an empty 200 array)
  when the batch has no hospitals; our client translates that into an empty result rather than
  surfacing it as a generic error. The downstream's own health check lives at `GET /`, not
  `/health`.
