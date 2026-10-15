# Utility Tariff Interpreter — MVP Execution Plan

Source of truth: `docs/PRD.md`. Technical contract: `docs/ARCHITECTURE.md`. This plan breaks the MVP into
small, independently verifiable steps. **Every step ships code + tests, passes `make check`, is reviewed, and
is committed before the next step starts.**

---

## A. How each step is executed (the gate)

For every step `Sxx` below:

1. **Implement** — read the step spec, the cited PRD sections and the relevant ARCHITECTURE sections; write
   code and tests; run the step's tests and `make check` until green. Scope discipline: implement what the
   step says, nothing from later steps (stubs only where a later step is named).
2. **Verify (in parallel)**
   - *Spec reviewer*: checks the diff against the step spec, PRD requirements and ARCHITECTURE invariants
     (§3); runs `make check`; reports concrete defects with severity (`blocker/major/minor`).
   - *Adversarial prober*: writes probe tests in `backend/tests/_probes/` (gitignored) or
     `frontend/src/__probes__/` targeting edge cases (boundaries, nulls vs zeros, units, ordering,
     immutability, idempotency, invalid transitions) and reports failures as defects.
3. **Fix** — address every blocker/major (and cheap minors); promote useful failing probes into the real test
   suite; re-run `make check`. Repeat verify→fix up to 3 rounds.
4. **Commit** — `make check` green → `git commit -m "Sxx: <title>"` (with session trailers). Pushed at the
   end of each phase.

**Definition of Done (all steps):** acceptance bullets satisfied; tests for each bullet exist with `req`
markers where applicable; `make check` green; no TODOs for in-scope behavior; ARCHITECTURE updated if a
contract detail had to change.

---

## B. Phases & dependency graph

```
Phase 0  S00 environment & scaffold
Phase 1  S01 → S02 → S03 → S04 → S05 → S06 → S07 → S08 → S09          (pure engine)
Phase 2  S10 → S11 → S12 → S13 → S14 → S15 → S16 → S17 → S18 → S19 → S20 → S21 → S22 → S23 → S24
Phase 3  S25 → S26 → S27 → S28 → S29 → S30 → S31 → S32                  (frontend on stable API + seed)
Phase 4  S33 → S34                                                      (E2E demo, final audit)
```

---

## C. Steps

### Phase 0 — Environment

#### S00. Environment & scaffold
- Backend `uv` project (`backend/pyproject.toml`) with packages `tariff_engine`, `tariff_app`; deps per
  ARCHITECTURE §1; ruff/mypy/pytest config; `tests/` tree; `req` marker registered; `.gitignore`
  (`.data/`, `tests/_probes/`, `node_modules`, `dist`, caches).
- Minimal FastAPI app with `GET /api/v1/meta`; smoke tests (engine import + meta endpoint).
- Frontend Vite React TS app, ESLint, Vitest, Testing Library, a smoke component test; dev proxy `/api`.
- Root `Makefile` (ARCHITECTURE §12), `CLAUDE.md`, `README.md`, `docs/requirements.yaml` (all IDs),
  GitHub Actions CI running `make check`; Claude Code SessionStart hook installing deps for cloud sessions.
- **Tests:** backend smoke, frontend smoke; `make check` green.

### Phase 1 — Deterministic engine (`backend/src/tariff_engine`)

#### S01. Decimals & units — PRD §9.1 "Fixed and linear charges", R05
- `decimals.py` (D(), engine_context, RoundingMode, quantize) and `units.py` (Unit, parse_unit, Quantity)
  exactly per ARCHITECTURE §4.1–4.2.
- No-float guard test (AST scan of `tariff_engine` for `float` usage and forbidden imports).
- **Tests:** float rejection; each rounding mode incl. negatives and .5 ties; cents↔USD normalization
  (`6.912 cents/kWh × 1000 kWh = 69.12 USD`); kW vs kWh incompatibility on `+`/compare; `kWh/kW = h`;
  `q(200,h) × 1500 kW = 300000 kWh`; alias label preservation; Hypothesis: `(a+b)−b == a`, unit
  algebra associativity/commutativity for `*`.

#### S02. Expression language — R03 "formula", §9.1
- `expressions.py` per ARCHITECTURE §4.3 (parser, whitelist, referenced_names, evaluate with Quantities,
  MissingInputError, positions in errors, size/depth limits).
- **Tests:** precision of literals (`0.1+0.2 == 0.3` exactly); `max(measured_demand_kw, 0.95 * peak_kw)`;
  ternary + `in_months`; unit errors surface the offending sub-expression; rejected constructs (`__import__`,
  attribute access, `**`, subscripts, lambdas, comprehensions, kwargs, unknown functions, huge input,
  deep nesting); `referenced_names` excludes function names/built-ins; division by zero → CalcError;
  missing input → MissingInputError naming the key.

#### S03. Rule model, validation & dependency graph — R03, R04, §9.1 "Dependencies and precedence"
- `model.py` Pydantic models (ARCHITECTURE §4.4), JSON round-trip with decimals as strings.
- `graph.py` (deps extraction across all calc types, deterministic topo order, cycle detection with path).
- `validate_version`, `validate_package` with issue codes; package merge with precedence + override reasons.
- **Tests:** valid sample version; each issue code triggered by a minimal bad fixture; cycle path reported;
  duplicate key w/o override → error, with override reason → resolved with reason recorded; open tier not
  last → error; by_period key format; JSON schema export of the model (used later by the rule editor).

#### S04. Primitive evaluators — §9.1 all rows
- Evaluators for `fixed, rate, tiered, formula, percentage, minimum_bill, lookback`, applicability, rounding,
  fallback, `by_period` parameter resolution, trace recording, used inputs/parameters with provenance.
- **Tests (each with hand-computed values):** cents vs dollars rate; tiered allocation with open final tier
  (each kWh allocated once; per-tier subtotals in trace); demand-linked blocks (`200 h × billing_demand`);
  `round_each_tier`; percentage on explicit base; minimum bill both binding and non-binding; lookback with
  `months_filter` summer-only, `include_current`, `min_points` shortfall → missing_input listing the exact
  missing months; baseline_input (all-time peak) participates in max; by_period missing month →
  missing_input naming the factor and month; applicability months; fallback to billed demand sets
  `fallback_used`. Hypothesis: tier allocation conserves quantity and is monotone.

#### S05. Calculator orchestration, coverage & check scope — R05, R06 (partial), §2, §8 rows "Missing rider / history", "Dates and applicability", "Quantity provenance"
- `calculator.py` per ARCHITECTURE §4.5: utility/territory gate, effective-version selection, cross-version
  `prorate_by_days` (or blocked with request), merge, topo evaluation, status propagation, coverage,
  expected_total vs supported_subtotal, deduped input requests, check scope.
- **Tests:** complete case; missing rider factor → only supported subtotal + specific request; missing
  history → specific months requested; dependency blocked propagation; awaiting_implementation →
  unsupported and partial; wrong entity/territory → blocked; cross-version without/with allocation;
  billed-demand-only → demand determination `unchecked`, measured+history → `checked`; determinism:
  same input in shuffled member/component order → identical JSON; reported vs derived provenance retained.

#### S06. Reconciliation, assessment & reference tolerance — R06, R07, §8 "Threshold boundary", "Zero and missing values", §9.2
- `reconcile.py` per ARCHITECTURE §4.6 incl. one-to-many groups, balance bridge, offsets, totals rule,
  `assess`, `classify_reference` (+ component overrides helper).
- **Tests:** PRD boundary: expected 100000, billed 101000 → not flagged (exactly 1%); 101001 → flagged;
  98999 → flagged (opposite direction), signed differences preserved; expected 0/billed 0 matched; expected
  0/billed nonzero flagged; missing expected → coverage gap, not zero; empty required → unresolved;
  offsetting +X/−X rows both visible with zero net; partial → no full expected total; unmapped current
  charge blocks full total; balance bridge arithmetic incl. negative credits; tolerance rule incl. ref=0,
  unset terms → pending, override precedence. Hypothesis: flag symmetric in sign; threshold monotonic.

#### S07. Synthetic tariff fixtures & golden reference cases — PRD §8 "Additional reference cases", §9 T1–T5
- `backend/fixtures/synthetic/tariffs/*.json` — SYNTHETIC packages mirroring patterns (values invented,
  labeled): `SYN-GA-PLL` (demand-dependent energy blocks, seasonal ratchet, contract floor, minimum bill,
  reactive demand, linked % cost-recovery riders), `SYN-ELL-LIPS` (demand tiers, energy, reactive,
  monthly fuel factor, facilities/loss terms, contract minimum, all-time-peak floor, closed to new
  business), `SYN-ELL-LPS` (seasonal demand pricing, fuel, voltage credit, transformer losses, power-factor
  adjustment, off-peak adjustment, contract floor), `SYN-FPL-GSLDT` (fixed, demand, TOU energy, fuel,
  conservation, capacity, environmental, storm, curtailment credit as awaiting_implementation),
  `SYN-GA-TOU-HLF` (TOU + eligibility conditions), and six Florida comparison candidates `SYN-FL-C1..C6`
  (one closed-to-new-business, one ineligible new enrollment, one with TOU determinants).
- `backend/fixtures/synthetic/reference_cases/*.json` — inputs + hand-computed expected component values
  with a short derivation note per component; one held-out period per package.
- **Tests:** every fixture validates; golden cases reproduce expected values exactly; required reference
  cases each present and passing: seasonal demand rule, demand-dependent energy block, minimum bill,
  voltage/loss adjustment, historical ratchet, variable rider, negative credit.

#### S08. Explanations — R08
- `explain.py` per ARCHITECTURE §4.7.
- **Tests:** each generator fires only on proving data and cites evidence; ambiguous difference →
  `unresolved` "No documented cause"; no explanation without evidence; combined rate+quantity case does not
  produce a false single cause; deterministic ordering.

#### S09. Comparison engine — R11, R12, §6 "Comparison output", §8 "Comparison and offsets"
- `comparison.py` per ARCHITECTURE §4.8.
- **Tests:** six candidates on a 12-month scenario; shared vs candidate-specific determinant compatibility
  (flat monthly kWh cannot fill on-peak kWh → partial with request); unconfirmed/ineligible/closed-without-
  evidence/reference-not-passed excluded from ranking with reasons; single-month reuse only with approval;
  baseline diffs; cheapest label wording names scenario, horizon, candidate set; scenario-estimate label when
  any assumed input; ties deterministic.

### Phase 2 — Persistence, services & API (`backend/src/tariff_app`)

#### S10. Database foundation
- Config (ARCHITECTURE §11), engine/session, `DecimalText`, UTC datetime type, id generator, Clock, all ORM
  models of ARCHITECTURE §5, Alembic initial migration, content-addressed file store, app factory with DI.
- **Tests:** Decimal round-trip exactness (e.g. `0.1`, `123456789012345678.123456789`); null vs zero; FK
  enforcement; migration == metadata (no drift); file store dedupe by sha256.

#### S11. Settings — R07, §7 "Workspace / settings", §9.2, D08
- Service + API: current settings, version history, update (threshold, abs/rel tolerance, component
  overrides) with reason → new version; defaults threshold `0.01`, tolerances unset.
- **Tests:** defaults; validation (threshold ≥ 0, ≤ 1; tolerances ≥ 0); history preserved; current = latest.

#### S12. Facilities, accounts, roster import & research candidates — R01, §9 "Coverage", Appendix A, D01
- CRUD for facilities/production units/accounts/account facts (provenance, effective dates); roster import
  CSV/XLSX → preview (row validation, duplicates, unknown columns) → confirm; research-candidate seeding
  for the 13 Appendix A locations (record_status `research_candidate`, utility/rate blank); explicit
  "confirm candidate → facility" action; scope classification for host-site/supplier accounts;
  per-account configuration-coverage summary (stub fields filled by later steps).
- **Tests:** import a 20-facility synthetic roster (CSV and XLSX) with multiple accounts per site; edit with
  provenance; candidates excluded from confirmed lists until confirmed; ambiguous/duplicate rows reported.

#### S13. Documents & source references — R02, R03, §7 "Source document", §9.3
- Upload (PDF/CSV/XLSX; size/type validation), sha256 dedupe (duplicate returns original), page text +
  line extraction, unreadable-page detection → input request, document viewer API (pages/lines),
  `source_ref` creation with quote validation, public URL snapshot metadata (source_url, retrieved_at).
- **Tests:** generated synthetic PDFs (fpdf2) incl. one blank/unreadable page; duplicate upload; quote
  not on page → 422; line locators stable.

#### S14. Tariff library, approval & account packages — R03, R04, §9.1, §9.3
- Schedules, versions (draft create/edit → copy-on-write from approved), validation on save, approval with
  reason/timestamp (blocked by validation errors), supersede on newer approval; component inventory with
  support states; source passages linked to components (via source_ref); packages + members + precedence
  reasons; account assignment (proposed → approved) with allocation rule; line mapping rules; import of
  S07 fixture JSON; affected-analyses query stub (filled in S16); optional AI rule-draft endpoint (mocked).
- **Tests:** approve/edit/copy-on-write; approval blocked with errors; wrong-territory assignment rejected;
  version selection by effective date; inventory shows supported vs awaiting_implementation; passage linkage.

#### S15. Invoice intake, extraction & corrections — R02, R01 (ambiguous assignment), §2 "Required inputs", D02/D05
- Heuristic extractor + optional Claude extractor (structured output; source-validated; mocked in tests);
  proposal → confirm (with corrections) → invoice + lines + input set (reported determinants with
  source refs, measured/billed/contract demand distinct); manual entry; corrections with
  `correction_event` and new input-set versions; duplicate rejection; revision → explicit supersede;
  ambiguous account match → `assignment_status=ambiguous` requiring confirmation; line mappings proposed
  from approved mapping rules; demand history series from prior invoices.
- **Tests:** synthetic invoice PDF extracts every field with locators; AI value not found in text dropped;
  correction history; duplicate vs revised; ambiguous assignment; manual entry provenance; null preserved.

#### S16. Analyses: run, snapshot, idempotency, staleness, states — R04, R05, §5 "Review states", §7
- Create analysis = resolve approved package for account+period, build snapshot (package defs, context,
  lines, mappings), run engine + reconcile + explain, store result, initial assessment. Fingerprint
  idempotency; rerun → new version (supersede link); staleness propagation hooks (tariff approval, input
  correction, invoice supersede, assignment change); affected-analyses listing; state machine with partial
  acknowledgment; reviewed = immutable (409 on mutation); reproducibility check endpoint.
- **Tests:** identical inputs reproduce identical amounts; retry returns same analysis; rule edit marks
  drafts stale but not reviewed; rerun preserves prior and records approved version used; invalid
  transitions rejected; unapproved assignment/rules prevent finalization.

#### S17. Assessments, explanation reviews & review decisions — R07, R08, §5
- Reevaluate with current settings → new assessment version (amounts reused); explanation statuses
  (documented / awaiting confirmation / unresolved) with user confirmation + user-authored explanations
  that must cite evidence; review decision (conclusion + notes) freezing the latest assessment.
- **Tests:** threshold change → previous assessment untouched, new version differs; user explanation without
  evidence rejected; review freezes; reviewed analysis rejects edits.

#### S18. Findings lifecycle — R09
- Findings from rows/missing items/coverage gaps; lifecycle states with events; notes & next action;
  confirmed billing error record; credit receipt requiring finding + supporting document; findings
  remain open after review.
- **Tests:** all transitions; distinct records for potential discrepancy / confirmed error / credit; credit
  without document rejected; finding survives analysis rerun and links to originating version.

#### S19. Evidence exports — R10
- CSV reconciliation (metadata header block + component rows + balance bridge + findings) and printable
  HTML evidence report (Jinja2, print CSS) for a specific analysis + assessment version; superseded/older
  analyses clearly labeled; synthetic labels; partial coverage, scenario estimates, unresolved issues shown.
- **Tests:** parse CSV and compare every number to the saved analysis exactly; report contains IDs,
  versions, scope notice, coverage, thresholds/tolerances, findings; older version banner.

#### S20. Reference cases, validation status & account enablement — §8 "Reference calculation", §9.2, §10.1 D06/D08, R04 "Account enablement"
- Reference case CRUD (from fixtures, or "save analysis as acceptance fixture"), runs classified with
  tolerances (pending when unset), tariff-version `reference_status`, held-out cases; per-account
  enablement checklist (package assembled, applicability approved, every invoice component mapped, support
  states, reference status, outstanding documents) — "unsupported complete labels block enablement".
- **Tests:** pass/fail/pending classification; override per component; enablement blocked by an
  unsupported component; export fixture JSON round-trips.

#### S21. Comparison scenarios service — R11, R12, D07
- Scenario CRUD with versioning and diff; candidates with eligibility status/evidence/grandfathering;
  shared and candidate-specific monthly inputs with provenance; approvals (single-month reuse, forecast);
  run → persisted immutable result via engine `compare`; planned facilities supported (no invoices).
- **Tests:** six-candidate scenario end-to-end; edited scenario keeps baseline version and shows diff;
  closed-to-new without existing-eligibility evidence excluded; runs immutable.

#### S22. Portfolio queue — §7 "Portfolio queue", R01
- `GET /portfolio/queue` (invoices × latest analysis/assessment) filterable by facility, utility, period,
  coverage, review state, threshold flag; accounts awaiting setup listed; research candidates separate;
  per-account configuration vs calculation coverage.
- **Tests:** each filter; 20 facilities; candidates segregated; awaiting-setup accounts visible.

#### S23. Synthetic demo workspace seed — PRD §8 "Use clearly labeled synthetic cases", §10.1 demo
- `python -m tariff_app.seed --reset`: settings, 13 research candidates, synthetic 20-facility roster with
  accounts across the five patterns, synthetic tariff packages + generated tariff PDFs with passages,
  approved assignments + mapping rules, 12 months of generated invoice PDFs per demo account (including a
  rate error, revised bill, duplicate, negative credit, missing rider factor, billed-demand-only, cross-
  version period), analyses, a few findings, six-candidate Florida comparison scenario, reference cases.
- **Tests:** seed runs twice idempotently (`--reset`); counts; every seeded analysis reproducible.

#### S24. Acceptance matrix & traceability — PRD §8 entire
- `tests/acceptance/` one module per matrix row + additional reference cases, through the service/API
  layer on seeded or fixture data; meta-test: every ID in `docs/requirements.yaml` has ≥1 test;
  `make traceability` generates `docs/TRACEABILITY.md`.
- **Tests:** the suite itself; meta-test green.

### Phase 3 — Frontend (`frontend/`)

#### S25. Frontend foundation
- `make openapi` + generated types + typed client; app shell (nav: Portfolio, Facilities, Tariffs, Comparisons,
  Findings, Reference cases, Settings), routing, Query client, error boundary, design tokens, shared
  components (ARCHITECTURE §9), `SyntheticBadge`, meta/AI availability banner.
- **Tests:** shared components (Money exact-tooltip & rounded label, Variance sign/flag, CoverageBadge states),
  routing smoke, API client error mapping; CI openapi drift check.

#### S26. Portfolio queue, facilities & roster import UI — R01, §7
- Queue with all filters + awaiting-setup + research candidates section; facility list/detail; account page
  (details, facts with provenance, documents, tariff versions, analyses, findings, configuration vs
  calculation coverage, enablement checklist); roster import wizard (upload → preview → confirm).
- **Tests:** filter interactions; candidate confirm action; import wizard error rows.

#### S27. Documents & invoice intake UI — R02
- Upload with duplicate/revision prompts; document viewer (pages/lines, unreadable pages); extraction review
  with source highlights; confirm/correct fields (units, dates, demand types) with reasons; manual entry;
  input-set versions + correction history; ambiguous assignment confirmation; mapping review.
- **Tests:** duplicate prompt; correction requires reason; measured/billed/contract demand distinct.

#### S28. Tariff library & reference cases UI — R03, R04, §9.2
- Schedules/versions/components inventory with support badges; component detail (formula, params, units,
  applicability, rounding, deps, exact passage); draft editing (structured JSON editor with validation
  issues); approve with reason; package + assignment approval; mapping rules; affected analyses + rerun;
  reference cases & runs with pass/fail/pending.
- **Tests:** approval disabled with validation errors; edit-approved creates draft; passage display.

#### S29. Invoice review screen — R05–R10, §5 tables
- The five-question layout; component table with signed diff/%/flag/check scope; evidence drawer (rule,
  passage, trace, invoice line source, inputs with provenance); explanations with statuses; input requests;
  supported subtotal vs full total; balance bridge; partial acknowledgment; decision; create finding from row;
  version history (analysis versions, assessments, reevaluate); exports (CSV, report).
- **Tests:** partial shows subtotal + uncovered and no full total; offsets visible; threshold label; ack required.

#### S30. Findings workspace — R09
- List/filter; detail with lifecycle transitions, notes, next action, evidence links, confirmed error, credit
  receipt with document upload/link.
- **Tests:** transitions; credit requires document.

#### S31. Comparison UI — R11, R12, §6
- Scenario list/editor (horizon, shared monthly inputs, approvals), candidates (eligibility, evidence,
  grandfathering, commitments, unmodeled conditions, specific inputs), run + results (components, totals,
  baseline diff, coverage, ranking, exclusion reasons, cheapest label, scenario-estimate label), versions diff.
- **Tests:** excluded candidates not ranked; labels; diff view.

#### S32. Settings UI — R07, §9.2, D08
- Threshold & tolerances (incl. component overrides) with reason and meaning text; version history;
  "reevaluate" guidance for affected analyses.
- **Tests:** validation; history rendering.

### Phase 4 — Integration

#### S33. Single-process serving & E2E demo — PRD §10.1 demo sequence, "Engineering completion checks"
- FastAPI serves built SPA (`make run`), Playwright E2E on a seeded workspace: (1) facility/account with
  entity/tariff/contract terms; (2) review a monthly invoice with quantities and clauses; (3) explain a
  difference, create a finding, export evidence; (4) compare six Florida candidates; (5) review rules,
  sources, reference results, set tolerances, save acceptance fixture. Plus 20-facility queue and threshold
  edit → reevaluate preserving prior assessment.
- **Tests:** the E2E suite (`make e2e`), screenshots saved as artifacts.

#### S34. Final audit & documentation
- Adversarial requirement-by-requirement audit (R01–R12, §5 tables, §7, §8, §9) against running code;
  fix gaps; README (setup, demo walkthrough), user guide, known limitations, traceability regenerated.
- **Tests:** full `make check` + `make e2e` green.
