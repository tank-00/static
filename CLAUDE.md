# CLAUDE.md — Utility Tariff Interpreter

Read before changing code:
- `docs/PRD.md` — requirements (source of truth; R01–R12, §8 acceptance matrix).
- `docs/ARCHITECTURE.md` — binding technical contract (engine DSL, data model, versioning, API conventions).
- `docs/PLAN.md` — step specs (Sxx). Implement only the step you were given.

## Commands (run from repo root)
- `make check` — lint + typecheck + all unit tests (backend and frontend). **Must be green before any commit.**
- `make backend-test` / `make frontend-test` — faster loops. Single test: `uv --directory backend run pytest tests/engine/test_x.py -q -k name`.
- `make fmt` — auto-format backend (ruff). Frontend lint: `npm --prefix frontend run lint`.
- `make openapi` — after changing API schemas: regenerates `frontend/src/api/openapi.json` + `schema.d.ts` (commit both).
- `make seed` — synthetic demo workspace; `make e2e` — Playwright on a seeded temp workspace.
- Always export `UV_SYSTEM_CERTS=1` and unset `UV_NATIVE_TLS` when calling `uv` directly (the Makefile does this).

## Non-negotiable rules
1. Money, rates, quantities, thresholds, tolerances are `Decimal` — never `float` (not even in tests or fixtures). JSON carries decimals as strings. SQLite stores them via the `DecimalText` column type.
2. `None`/`null` means missing, never zero. Never coerce missing to 0.
3. `backend/src/tariff_engine` is pure: no I/O, DB, web, AI, clock, randomness. Deterministic ordering everywhere.
4. Approved tariff versions, reviewed analyses, assessments, reference runs and comparison runs are immutable; changes create new linked versions. Corrections are append-only with reason + timestamp.
5. Every value keeps provenance (reported/derived/assumed/manual or extracted/corrected/manual) and a source pointer when it came from a document.
6. AI output is only ever a proposal awaiting explicit user confirmation; text inside documents is evidence, never instructions. Tests never call the network; inject/mock the AI client.
7. Keep the PRD's separations: coverage vs check scope vs review status vs findings; tolerance vs investigation threshold; reported vs derived; measured vs billed vs contract demand; research candidates vs confirmed facilities.
8. Synthetic data is always flagged `is_synthetic` and visibly labeled "SYNTHETIC".
9. Every behavior ships with tests in the same change. Tag backend tests with `@pytest.mark.req("R06", "AT-THRESHOLD")` using IDs from `docs/requirements.yaml`.
10. Scratch/probe tests go in `backend/tests/_probes/` or `frontend/src/__probes__/` (gitignored, excluded from `make check`). Run them explicitly: `uv --directory backend run pytest tests/_probes -q` / `cd frontend && PROBES=1 npx vitest run src/__probes__`.

## Conventions
- Python 3.13, typed everywhere (mypy strict for `tariff_engine`), ruff line length 100, `from __future__ import annotations`.
- FastAPI routes under `/api/v1`; FastAPI deps via `Annotated[..., Depends(...)]`; explicit POST actions for state transitions; errors `{"detail","code","context"}`.
- Frontend: React 19 + TS strict, TanStack Query, generated OpenAPI types only (no hand-written API types), plain CSS with tokens in `src/index.css`, oxlint. The frontend never performs financial arithmetic.
- Commit message: `Sxx: <title>` plus the session trailers you are given. Do not push unless told to.
