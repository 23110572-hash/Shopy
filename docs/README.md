# Shopy

Shopy is a production-shaped, single-merchant autonomous-commerce application for Razorpay Track 01. The approved behavior and implementation sequence live in [`architecture.md`](architecture.md).

## Repository layout

- `backend/` — FastAPI application, operational validation scripts, pinned Python requirements, and the ignored local virtual environment
- `frontend/` — React + TypeScript + Vite application
- `database/` — Alembic migration source and database operations; the live database itself is hosted by Neon
- `docs/` — architecture and project documentation
- repository root — deployment and shared tool configuration

## Foundation stack

- Python 3.12
- FastAPI
- PostgreSQL on Neon
- SQLAlchemy 2 with asyncpg for application sessions
- Alembic with psycopg 3 for synchronous migrations
- OpenRouter behind an application-owned LLM gateway
- Razorpay test mode behind an application-owned payment gateway
- React, TypeScript, and Vite

## Dependency manifests

- `backend/requirements.txt` fully pins the Render/backend runtime graph.
- `backend/requirements-dev.txt` includes the runtime graph plus pinned Ruff and mypy tooling.
- `frontend/package.json` pins direct frontend packages and `frontend/package-lock.json` locks the complete npm graph used by Vercel's `npm ci` install.

## Local setup

Run all commands below from the repository root.

1. Create an ignored root `.env` and configure the server-side values listed below. Never commit or print this file.
2. Create and activate the backend-local Python 3.12 virtual environment, then install the pinned development graph:

   ```powershell
   python -m venv backend/.venv
   .\backend\.venv\Scripts\Activate.ps1
   python -m pip install --requirement backend/requirements-dev.txt
   ```

3. Apply the tracked schema migrations to Neon:

   ```powershell
   python -m alembic -c database/alembic.ini upgrade head
   ```

   Neon stores the live rows, but `database/alembic.ini` and `database/migrations/` must remain in source control so Render and future releases can verify and evolve that schema. Local exports and `database/data/` are ignored.

4. The verified catalogue already lives in Neon and is never recreated at application startup. If a new database must be initialized, restore an approved catalogue source locally before running the explicit seed command:

   ```powershell
   python -m database.scripts.seed_catalog
   ```

5. Run backend validation:

   ```powershell
   python -m ruff check --target-version py312 --line-length 100 --select E,F,I,B,UP,SIM,RUF backend database
   python -m mypy backend database/migrations/env.py
   python -m backend.scripts.validate_foundation
   ```

6. Start the API manually:

   ```powershell
   python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
   ```

7. Install the exact frontend graph and build it:

   ```powershell
   npm --prefix frontend ci
   npm --prefix frontend run build
   ```

   For local interactive development, run `npm --prefix frontend run dev` manually, then open `http://localhost:5173`.

## Required environment variables

`DATABASE_URL` is required immediately. `FRONTEND_ORIGIN` controls the only trusted browser origin. `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` are required when the intent parser is enabled. `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_WEBHOOK_SECRET` are required when payment integration is enabled. Signing and encryption secrets are separate and become mandatory with their respective features.

The application rejects Razorpay live keys. Test doubles belong only in isolated validation code and are never selectable by runtime configuration.

## Secret handling

- `.env` and `.env.*` are ignored. No environment example containing deployment structure is committed.
- Health responses never expose URLs, credentials, or provider error details.
- Rotate any credential pasted into chat, logs, screenshots, or issue trackers before deployment.

## Render and Vercel deployment

The deployment keeps all provider and database secrets on Render. Vercel receives only the non-secret Render API origin and proxies browser API requests through the Vercel origin, which keeps the existing HttpOnly session and CSRF cookies same-origin from the browser's perspective.

1. Create the Vercel project with **Root Directory** set to `frontend`. Reserve or note its production URL, but do not put backend secrets in Vercel.
2. Create a Render Blueprint from the repository-root `render.yaml`. Supply every value marked `sync: false` in the Render Dashboard. `FRONTEND_ORIGIN` must be the exact HTTPS Vercel production origin, with no path or trailing slash. Use the Neon TLS URL for `DATABASE_URL`.
3. Render installs `backend/requirements.txt` with pip, runs tracked Alembic migrations in `preDeployCommand`, starts FastAPI on Render's `$PORT`, and checks `/health`. Catalogue seeding remains an explicit operation and is never run automatically during deployment.
4. In Vercel, set `RENDER_API_ORIGIN` to the deployed Render origin, such as `https://your-shopy-api.onrender.com`, with no path. Do not set `VITE_API_BASE_URL` in production; the frontend intentionally uses same-origin `/api` and `/health` rewrites.
5. Deploy Vercel. Configure the Razorpay Test Mode webhook URL directly against Render as `https://<render-host>/api/webhooks/razorpay` after that route is available. Store the matching webhook secret only in Render as `RAZORPAY_WEBHOOK_SECRET`.

Vercel preview URLs are not automatically trusted for authenticated mutations. Production accepts only the exact `FRONTEND_ORIGIN`; change it deliberately if a preview must exercise account writes. The local development defaults remain available only when `APP_ENV=development`.

Platform references: [Render Blueprint YAML](https://render.com/docs/yaml-spec), [Render FastAPI deployment](https://render.com/docs/deploy-fastapi), [Vercel Vite deployment](https://vercel.com/docs/frameworks/vite), and [Vercel programmatic configuration](https://vercel.com/docs/project-configuration/vercel-ts).
