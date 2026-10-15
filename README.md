# Utility Tariff Interpreter (MVP)

Helps an energy manager determine what a facility **should** pay under its electricity tariff, explain each
calculated charge, and compare a small set of eligible tariff options. See `docs/PRD.md`.

- **Primary workflow:** monthly invoice validation. Approved, source-linked tariff rules are applied to
  invoice-reported quantities. Each line is reconciled, flagged against an editable 1% threshold, explained
  from evidence, tracked as a finding, and exported.
- **Secondary workflow:** bounded tariff comparison. Up to six (configurable) candidates are compared on one
  explicit operating scenario, with eligibility evidence.

> All bundled tariffs, invoices, rosters and scenarios are **SYNTHETIC**. They mirror the rule *patterns* of
> the public tariffs cited in the PRD, not their actual rates. Real account data comes from the customer.

## Layout

| Path | What |
|---|---|
| `backend/src/tariff_engine` | Pure, deterministic calculation engine (Decimal arithmetic, units, rule DSL, reconciliation, comparison) |
| `backend/src/tariff_app` | FastAPI app, SQLite persistence (SQLAlchemy + Alembic), extraction, services, exports, seed |
| `frontend/` | React + TypeScript SPA (Vite, TanStack Query, generated OpenAPI client) |
| `docs/` | PRD, architecture contract, execution plan, requirement IDs, traceability |

## Quick start

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), Node 22.

```bash
make setup      # install backend + frontend dependencies
make check      # lint + typecheck + unit tests (backend and frontend)
make seed       # load the synthetic demo workspace
make dev        # API on :8000, web on :5173
```

Other targets: `make e2e` (Playwright on a seeded temp workspace), `make run` (single process serving the
built SPA on :8000), `make openapi` (regenerate frontend API types), `make help`.

AI-assisted extraction/explanations are optional. Set `ANTHROPIC_API_KEY` (and optionally
`TARIFF_AI_MODEL`) to enable them. Without it, deterministic extraction and manual entry are used.

## Status

Built step by step following `docs/PLAN.md`. Each step lands with tests, a passing `make check`, an
independent review and a commit.
