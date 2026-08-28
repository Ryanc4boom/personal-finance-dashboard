# Working in this repo

A personal finance app wired to **real bank accounts** via Plaid, in a **public**
GitHub repository. Those two facts drive everything below: the data is real
money, and every commit is world-readable the moment it is pushed.

`.cursorrules` and `AGENTS.md` are symlinks to this file. Edit this one.

---

## Non-negotiables

### 1. Never hardcode secrets

Every credential comes from `.env` via `app/core/config.py` (pydantic-settings).
Never inline a key, token, connection string or password — not in source, not in
a test fixture, not in a comment, not in a commit message.

- `.env`, `.env.local` and `*.sql` are gitignored. Never `git add -f` them.
- A `pg_dump` of this database contains live Plaid access tokens and every real
  transaction. It is a worse leak than `.env`.
- New settings go in `Settings` **and** `.env.example` with a blank value.
- `.githooks/pre-commit` blocks the common shapes. Do not bypass it with
  `--no-verify`; fix the finding or narrow the rule.

**Commit messages are as public as code.** The only real leak this repo has had
was a commit body quoting an actual net worth. State verification results as
counts, ratios, or "idempotent on re-run" — never balances, account numbers,
institution item ids, or emails.

### 2. Check object-level ownership before returning or mutating user data

Ownership runs `user → item → account → transaction`. Every query touching
user data filters on it. There is currently one seeded user, which is exactly
why this is easy to get wrong: **with one user, a query returns the right answer
whether or not it filters**, so a missing filter is invisible until it is a
breach.

```python
# Wrong — finds the row by id alone.
item = db.scalar(select(Item).where(Item.provider_item_id == provider_item_id))

# Right — scoped to the caller.
item = db.scalar(
    select(Item).where(
        Item.provider_item_id == provider_item_id,
        Item.user_id == user.id,
    )
)
```

Never accept a `user_id` or email from the client to select whose data to return.
Resolve the user with `services.users.get_current_user`, and derive everything
from it. Answer **404** rather than 403 for a resource the caller does not own —
403 confirms the id exists.

Add a cross-user test in `tests/test_object_ownership.py` for any new endpoint
that takes a resource id.

### 3. Authentication and CSRF

`app/core/auth.py` requires a shared secret in `X-API-Key` on every request.
This is **not** user authentication — it is one key for one user — and it must
not be mistaken for one when adding features.

The header is also the CSRF defence: a custom header cannot be sent
cross-origin without a preflight, and the preflight fails for any origin not in
`CORS_ORIGINS`. So:

- **Never make a state-changing endpoint reachable without the key.** Any new
  exemption in `EXEMPT_PATHS` needs a stronger check in its place — the Plaid
  webhook is exempt only because it verifies an ES256 signature over the body.
- **Be wary of optional request bodies on POST.** `payload: Foo | None = None`
  means an empty body parses, and an empty POST with a form content-type is a
  CORS simple request that skips preflight. That is how `/api/v1/plaid/sync`
  became triggerable from any page. Prefer a required body.
- **If cookie-based sessions are ever added, add real CSRF tokens.** The header
  trick stops working the moment there is an ambient credential the browser
  attaches automatically. Double-submit cookie or `SameSite=Strict` + a
  synchroniser token, on every POST/PUT/PATCH/DELETE.

### 4. Security headers

`app/core/headers.py` sets CSP, `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy` and `Permissions-Policy` on every response, and HSTS only over
https. Do not weaken these, and do not send HSTS over http — it pins
`localhost` in the browser and breaks unrelated projects on the machine.

The API's CSP (`default-src 'none'`) is not the frontend's. A Next.js CSP
belongs in `next.config.ts`.

### 5. Parameterised queries only

Use the ORM or bound parameters. Never build SQL with f-strings or
concatenation, including in the ingestion pipeline and the XBRL parser.

```python
db.execute(text("SELECT ... WHERE key = :key"), {"key": key})   # yes
db.execute(text(f"SELECT ... WHERE key = '{key}'"))             # never
```

### 6. Rate limiting

`app/core/rate_limit.py` throttles every route, with tighter buckets for Plaid
(billed per call) and research (SEC bans by IP). A new route that calls a paid or
rate-limited third party needs its own bucket in `_BUCKETS` — do not let it
inherit the generous default.

### 7. Validate input strictly

Request bodies inherit `app.schemas.StrictRequest` (`extra="forbid"`), so unknown
fields are a 422 rather than being silently dropped. New request schemas must use
it; there is a test that enumerates them.

### 8. Destructive actions need explicit confirmation

Every delete this app exposes is a hard delete server-side — no archive, no
undo. Route them through `ConfirmDestructiveModal`, which names what is lost and
puts focus on cancel rather than confirm.

Unlinking a bank, deleting transaction history and revoking a Plaid item are a
heavier class: irreversible *and* not re-derivable from Plaid. A plain modal is
not a high enough bar for those — pass `confirmPhrase` so the user has to type
the name back. **No such endpoint exists yet.** If you are adding one, that is
the bar.

### 9. Telemetry forwards nothing sensitive

`app/core/telemetry.py` and `lib/telemetry.ts` scrub before anything leaves the
machine. The reporter's default is to send request bodies, headers and frame
locals, which here are Plaid tokens, the `X-API-Key`, and ORM rows holding
balances.

- Drop whole sections rather than filtering keys. A deny-list needs editing
  every time a schema grows a column, and forgetting is silent.
- Never raise `SENTRY_TRACES_SAMPLE_RATE` without a span scrubber — spans carry
  SQL and its bound parameters.
- Report through `captureError`, not bare `console.error`. Read
  `tests/test_telemetry.py` before changing either module.

---

## Money and data conventions

These are correctness requirements, not style:

- **Money is integer cents** (`amount_cents`, `BigInteger`). Never floats. A DB
  `CHECK` ties `direction` to the sign of `amount_cents`.
- **Ingestion is idempotent.** Re-running a sync must not duplicate or resurrect
  rows. Pending→posted promotion keeps `pending_provider_txn_id` so a replayed
  delta cannot revive a ghost.
- **Raw provider payloads are preserved** in `raw_transaction`.
- Access tokens are Fernet-encrypted at rest (`app/core/security.py`) and must
  never be logged, returned in a response, or written to a fixture.

## Testing

- `cd backend && .venv/bin/python -m pytest`
- Tests requiring Postgres run against `budgeting_test` and **skip** when it is
  unreachable. They must never touch the development database.
- Security-critical changes need a test asserting the **negative** case. A check
  that accepts everything passes any happy-path test.

## Before you finish

- [ ] No secret in the diff or the commit message
- [ ] Every new query touching user data filters on ownership
- [ ] New endpoints are gated, bucketed, and use a strict request schema
- [ ] New deletes go through a confirmation, not a single click
- [ ] `pytest` passes and `npx tsc --noEmit` is clean in `frontend/`
