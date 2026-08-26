# PGv2 Postgres Integration — Phase 1 Runbook

**Scope:** Add PostgreSQL connectivity to the pgv2 two-service POC (`pgv2-backend` + `pgv2-frontend`), dev environment only. Redis is deferred to phase 2 (see `feature/redis-phase2` branch in `pgv2-backend`).

**Environment:** AWS account `833179915312`, region `us-east-2`, VPC `vpc-ca02dda3`. ECS cluster `pgv2-poc-cluster`, services `pgv2-poc-backend-service` / `pgv2-poc-frontend-api`.

---

## 1. Resource inventory (dev)

| Resource | Name / ID |
|---|---|
| RDS instance | `pgv2-poc-postgres` |
| RDS endpoint | `pgv2-poc-postgres.cbgtpwstj4ot.us-east-2.rds.amazonaws.com` : `5432` |
| DB name / user | `pgv2` / `pgv2` |
| RDS security group | `pgv2-poc-postgres-sg` (`sg-0ed5c8bbdfdc729b7`) |
| DB subnet group | `pgv2-poc-db-subnet-group` (subnets `subnet-0a577d40a6d9f3691` us-east-2a, `subnet-09330cb1c5a9acaaf` us-east-2b) |
| KMS CMK | `alias/pgv2-poc-dev-cmk` (`arn:aws:kms:us-east-2:833179915312:key/b2312c85-3375-48d9-9bee-6edce1307209`) |
| Secrets Manager secret | `pgv2-backend/dev-config` (`arn:aws:secretsmanager:us-east-2:833179915312:secret:pgv2-backend/dev-config-sshg37`) — keys `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` |
| Backend SG | `pgv2-poc-backend-sg` (`sg-0e494ed979f7292b1`) |
| ECS execution role | `hyventur-demo-ecs-execution` (inline policy `hyventur-demo-ecs-executionPolicy` grants `secretsmanager:GetSecretValue` + `kms:Decrypt` on the secret/CMK above) |
| Verification VM | `pgv2-poc-visual-test-vm` (`i-0a4292ebc3f691abb`), SG `pgv2-poc-vm-public-sg` (`sg-01c9aab992fec1af9`), public IP `18.217.95.177` (RDP/xrdp) |
| CodeBuild (backend) | `pgv2-backend-deploy-test` — clones via `BRANCH_NAME` env var override, builds, Inspector-gates, registers new task def revision, deploys |
| CodeBuild (frontend) | `pgv2-frontend-deploy-test` — same pattern |
| Git branches | `feature/postgres-phase1` in both `pgv2-backend` and `pgv2-frontend` (pushed to GitHub) |

---

## 2. Infra setup steps (as performed)

1. **Security groups**
   - Created `pgv2-poc-postgres-sg`, inbound 5432 from `pgv2-poc-backend-sg`.
   - Added egress from `pgv2-poc-backend-sg`: 5432 → `pgv2-poc-postgres-sg` (and 6379 → a Redis SG, pre-staged for phase 2, currently unused).

2. **RDS PostgreSQL**
   - DB subnet group `pgv2-poc-db-subnet-group` across both private subnets.
   - Standard create (not Easy create — Easy create hides instance class/storage/connectivity/initial-DB-name fields), `db.t3.micro`, Dev/Test template, **Initial database name: `pgv2`** (must be set explicitly or no database exists), VPC security group = `pgv2-poc-postgres-sg` only, Public access **No**.
   - ⚠️ **Gotcha hit:** the console defaulted to **"Manage master credentials in AWS Secrets Manager"** (RDS-generates its own password in its own secret), which silently made the password typed at creation time irrelevant and caused every app connection to fail auth (502 on every DB call, no obvious error). **Fix:** RDS → Modify → uncheck "Manage master credentials in AWS Secrets Manager" → set a new master password → Apply immediately → sync that exact password into the `pgv2-backend/dev-config` secret's `DB_PASSWORD` → force new ECS deployment.

3. **KMS + Secrets Manager**
   - Created CMK `pgv2-poc-dev-cmk` (key users left empty — execution role gets `kms:Decrypt` via its own identity policy, not by being an explicit key user, same pattern as QA).
   - Created secret `pgv2-backend/dev-config` (key/value pairs: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`), encrypted with the new CMK.
   - Added the secret ARN + CMK ARN to `hyventur-demo-ecs-executionPolicy`'s `Resource` array (existing statement, alongside the old unrelated hyventur-POC entries — left untouched).
   - Task definition → container `backend-service` → Environment variables, each `DB_*` row set to **Value type = ValueFrom** → `arn:...:secret:pgv2-backend/dev-config-sshg37:<KEY>::` (the JSON-key-selector syntax; trailing `::` leaves version stage/id unpinned so it always resolves current).
   - No new VPC endpoints were needed — `secretsmanager` and `kms` interface endpoints already existed in this VPC from the QA build, and the shared endpoint SG already allowed the backend SG inbound on 443.

4. **Deploy pipeline**
   - Actual working CI/CD is **AWS CodeBuild**, not the GitHub Actions workflow file (that one has unrelated WIP changes sitting uncommitted — leave it alone).
   - `pgv2-backend-deploy-test` / `pgv2-frontend-deploy-test` each clone via a project-level `BRANCH_NAME` env var (defaults: backend=`develop`, frontend=`main` — inconsistent with each other, not fixed as part of this work).
   - To build the feature branch instead of the default, override per-build (does **not** change the project's default for other people's builds):
     ```
     aws codebuild start-build \
       --project-name pgv2-backend-deploy-test \
       --environment-variables-override name=BRANCH_NAME,value=feature/postgres-phase1,type=PLAINTEXT \
       --region us-east-2
     ```
     (same for `pgv2-frontend-deploy-test`)
   - The backend buildspec's deploy step `describe-task-definition`s the **current live** task def and only swaps the image — so the Secrets Manager wiring (step 3) must already be saved as the latest revision *before* kicking off a CodeBuild run, or the new image deploys without DB credentials.

---

## 3. App changes (phase 1)

**`pgv2-backend/app.py`**
- New env vars: `DB_HOST`, `DB_PORT` (5432), `DB_NAME`, `DB_USER`, `DB_PASSWORD`.
- `GET /db-check` — opens a connection, `SELECT 1`, returns latency; `502` + error string on failure.
- `_get_conn()` helper — connects and runs `CREATE TABLE IF NOT EXISTS notes (id SERIAL PRIMARY KEY, message TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())` (idempotent, no separate migration step needed for this POC).
- `POST /notes` — inserts `{"message": "..."}`, returns the created row.
- `GET /notes` — returns the 50 most recent rows.

**`pgv2-frontend/app.py` + `templates/index.html` + `static/style.css`**
- `get_notes()` — relays `GET {BACKEND_URL}/notes`.
- `POST /notes` route — reads the HTML form's `message` field, relays `POST {BACKEND_URL}/notes`, redirects back to `/` (Post/Redirect/Get).
- Index page: new "Notes (Postgres via backend-service)" card — a text input + Save button, and the list of existing notes rendered below it.

---

## 4. Testing scenario (UI-driven, end-to-end)

This exercises the real path (browser → frontend-api → backend-service → RDS) rather than just a connectivity ping, then independently confirms the write against the database itself.

1. Browse to `http://frontend-api.pgv2-poc.local:8000/` (from a host inside the VPC — e.g. the RDP VM).
2. Confirm the "Connection to backend-service" card is green.
3. In the "Notes" card, type a message → **Save to DB**.
4. Page redirects back to `/` — the note should appear in the list with an `id` and `created_at` timestamp, and the card should be green (not red/502).
5. **Independently verify at the DB layer** (don't just trust the UI) — see §5.

---

## 5. DB-side verification (via the VM, `psql`)

RDS is private (no public access, not Aurora, so no console Query Editor exists for it). The verification VM already sits in the same VPC, so this is the practical way to query the table directly.

**One-time setup on the VM's security group (`pgv2-poc-vm-public-sg`, `sg-01c9aab992fec1af9`):**
- Outbound rule: `HTTP` (80) → `0.0.0.0/0` — needed for `apt-get` to reach package mirrors.
- Outbound rule: `PostgreSQL` (5432) → Custom → `pgv2-poc-postgres-sg` — needed for `psql` to reach RDS.
- (RDS side) Inbound rule on `pgv2-poc-postgres-sg`: `PostgreSQL` (5432) from Custom → `pgv2-poc-vm-public-sg`.

**On the VM (terminal, not browser):**
```
sudo apt-get update && sudo apt-get install -y postgresql-client
```

**Query:**
```
psql -h pgv2-poc-postgres.cbgtpwstj4ot.us-east-2.rds.amazonaws.com \
     -p 5432 -U pgv2 -d pgv2 \
     -c "SELECT id, message, created_at FROM notes ORDER BY id DESC LIMIT 5;"
```
Enter the RDS master password when prompted (interactively — never paste it into chat/logs). A real row coming back here, independent of what the app UI reports, is the actual confirmation the write persisted.

> Note: ECS Exec (`aws ecs execute-command`) was also explored as a way to shell into the running container and query from there, but was abandoned in favor of the VM+`psql` path above — it needs `ssmmessages:*` permissions on the **task role** (`hyventur-demo-ecs-task`) which were never actually added. Not required for the app to function; only relevant if this debugging path is revisited later.

---

## 6. Known follow-ups / not yet done

- Redis (phase 2) — branch `feature/redis-phase2` in `pgv2-backend` has a starting point (combined Postgres+Redis commit); needs rebasing to a clean Redis-only diff once phase-1 merges to `main`.
- `pgv2-backend`'s `.github/workflows/deploy.yml` has unrelated uncommitted WIP (SHA-tagged images, Inspector severity gate, ECS render/deploy actions) — not part of this work, left as-is.
- `BRANCH_NAME` defaults differ between the two CodeBuild projects (`develop` vs `main`) — inconsistent, not addressed here.
- Dev's other env vars (`S3_BUCKET`, `IMAGE_KEY`, etc.) are still plain task-definition values, not in Secrets Manager — only the new `DB_*` values were migrated as part of this work.
