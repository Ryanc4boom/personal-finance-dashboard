# Budgeting Platform — Phase 1

System foundation, database schema, Plaid ingestion engine, and transaction ledger UI.

```
.
├── docker-compose.yml        # PostgreSQL (TimescaleDB) + Redis + optional API container
├── backend/                  # FastAPI + SQLAlchemy + Alembic
│   ├── alembic/versions/     # 0001_initial_schema.py
│   └── app/
│       ├── core/             # config, db session, redis, encryption
│       ├── models/           # user, institution, item, account, transaction, …
│       ├── schemas/          # pydantic request/response models
│       ├── routers/          # plaid, accounts, transactions
│       └── services/         # ingestion, matching, normalize, plaid_client
└── frontend/                 # Next.js App Router + Tailwind + lucide-react
```

---

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Docker Desktop | any recent | Verified on 29.6.2 / Compose v5.3.1. Alternatively, point `DATABASE_URL`/`REDIS_URL` at your own Postgres 14+/Redis |
| Python | 3.11+ | 3.14 verified |
| Node.js | 20+ | 24 verified |

---

## Step-by-step: running locally in Plaid Sandbox

### 1. Configure environment

```bash
cp .env.example .env
```

Generate an encryption key (Plaid access tokens are encrypted at rest with it) and paste it into `.env` as `ENCRYPTION_KEY`:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then add your sandbox credentials from the [Plaid dashboard](https://dashboard.plaid.com/developers/keys):

```
PLAID_CLIENT_ID=<your client id>
PLAID_SECRET=<your *sandbox* secret>
PLAID_ENV=sandbox
```

### 2. Start Postgres + Redis

```bash
docker compose up -d db redis
```

The `db` image is `timescale/timescaledb:latest-pg16`; the migration enables the `timescaledb` extension and turns `balance_snapshot` into a hypertable.

### 3. Run migrations and start the API

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000
```

API docs: <http://localhost:8000/docs> · Health: <http://localhost:8000/health>

### 4. Start the frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Open <http://localhost:3000>.

### 5. Link a sandbox bank

1. Click **Link account**.
2. Pick any institution (e.g. "First Platypus Bank").
3. Sandbox credentials: username `user_good`, password `pass_good`. If prompted for MFA, use `1234`.
4. On success the backend exchanges the token and immediately runs a full backfill. Balances and transactions appear straight away.
5. **Sync** re-runs the cursor-based delta pull at any time.

### Optional: run the API in Docker too

```bash
docker compose --profile full up -d
```

This builds `backend/`, runs `alembic upgrade head`, and serves on port 8000 with hot reload.

### Optional: webhooks

Plaid can only reach a public URL. Expose the API (e.g. `ngrok http 8000`), then set in `.env`:

```
PLAID_WEBHOOK_URL=https://<your-tunnel>/api/v1/plaid/webhook
```

Re-link the item so the webhook is registered. `SYNC_UPDATES_AVAILABLE` then triggers a sync automatically.

---

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/plaid/link-token` | Create a Link token for the Plaid Link flow |
| POST | `/api/v1/plaid/set-access-token` | Exchange `public_token`, persist the item, run initial backfill |
| POST | `/api/v1/plaid/sync` | Cursor-based delta sync for one item or all items |
| POST | `/api/v1/plaid/webhook` | Plaid webhook receiver |
| GET | `/api/v1/accounts` | Balances split into cash vs. credit liabilities |
| GET | `/api/v1/transactions` | Ledger with `search`, `account_id`, `account_type`, `start_date`, `end_date`, `is_pending`, `limit`, `offset` |
| GET | `/api/v1/transactions/{id}` | Single transaction |

---

## Design decisions worth knowing

**Integer cents everywhere.** Every currency column is `BIGINT` holding minor units. Conversion happens once, in `services/normalize.py`, via `Decimal(str(amount))` — building a `Decimal` from the float directly would inherit binary float error. No float ever touches a monetary value.

**One sign convention, applied at the boundary.** Plaid reports a purchase as *positive* (money leaving) and a deposit as *negative*. That is flipped exactly once at ingestion so that internally `amount_cents > 0` always means money in and `< 0` money out. A database `CHECK` constraint enforces that `direction` and the sign never disagree, so a bad write fails loudly rather than silently corrupting totals.

**Raw payloads are preserved.** Every provider payload lands in `raw_transaction` (append-only, JSONB) *before* normalisation. If a normalisation bug ships, it can be replayed from there — re-pulling from Plaid is not an option once the cursor has advanced.

**Ingestion is idempotent.** All writes key on `provider_txn_id` (unique index). A duplicate webhook, a retried request, or a cursor rewind converges to the same rows instead of duplicating them.

**The cursor is crash-safe.** `item.cursor` is committed in the *same* database transaction that applies that page's changes, so the cursor is never ahead of durable data. A crash mid-pagination resumes exactly where it left off.

**Pending → posted collapsing.** Banks issue a pending authorisation and later a *separate* posted transaction with a new id; naive ingestion shows both and double-counts. `pending_transaction_id` from Plaid is used when present. Otherwise `services/matching.py` falls back to a heuristic requiring **all** of: same account, amount within ±1¢, date within 4 days, and one normalised description containing the other. Descriptions are stripped of processor noise and digit runs, since store/terminal numbers routinely differ between the two copies. It is deliberately conservative — a false match silently erases a real transaction, which is worse than showing a duplicate.

On promotion the existing row is updated **in place** rather than replaced, so any notes, tags, or budget exclusions the user already applied to the pending row survive. The old id is kept in `pending_provider_txn_id` so a replayed delta cannot resurrect the ghost row.

**Concurrent syncs are serialised.** Plaid's cursor model is not concurrency-safe — two workers reading the same cursor would both replay the same delta. A Redis lock per item prevents a webhook and a manual sync from racing.

**Access tokens are encrypted at rest** with Fernet (`ENCRYPTION_KEY`), never logged in plaintext.

**Traceability in the UI.** Every balance on screen is a button. Clicking *Depository Cash* or *Credit Card Liabilities* filters the table to that account class; clicking an individual account chip filters to that account. No number is shown that you cannot drill into.

---

## Phase 1 scope notes

- **Auth is not implemented.** The API operates as a single user seeded from `DEV_USER_EMAIL`. The ownership chain (`user → item → account → transaction`) is real and already enforced in every query, so adding real auth later means replacing `services/users.get_current_user` — not reworking the schema.
- **`transaction.category_id`** holds Plaid's personal-finance-category slug. It is intentionally not a foreign key yet; a `category` entity was not in the Phase 1 spec.
- **`is_recurring`** is present and user-editable but not yet populated by ingestion — that needs Plaid's `/transactions/recurring` endpoint.
