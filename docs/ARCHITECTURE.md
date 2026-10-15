# Utility Tariff Interpreter — Architecture Contract (MVP)

This document is the **binding technical contract** for every implementation step in `docs/PLAN.md`.
Requirements come from `docs/PRD.md` (cited as `PRD §n` / `R01`–`R12`). If this document and the PRD
disagree, the PRD wins and this document must be corrected in the same change.

---

## 0. Product summary (what we are building)

A single-user web application for an energy manager (Michael) covering ~20 facilities that:

1. Holds a facility/account workspace (R01) and ingests invoices, tariffs and rosters with
   source-linked extraction and user confirmation (R02).
2. Stores tariffs as **structured, versioned, approved rule components** with exact source passages (R03, R04).
3. Executes approved rules **deterministically** on invoice-reported quantities (R05), reconciles them
   line-by-line against the bill (R06), flags differences against an editable 1% threshold (R07),
   explains differences from evidence (R08), tracks findings (R09) and exports evidence (R10).
4. Compares a bounded set of candidate tariffs on a common, explicit operating scenario (R11, R12).

Non-goals for V1 (PRD §1 "Deferred"): interval-data reconstruction, automatic tariff discovery, rate-case
monitoring, other commodities, procurement, enterprise integrations, multi-user administration, auth.

---

## 1. Technology stack

| Layer | Choice | Notes |
|---|---|---|
| Language (backend) | Python 3.13 | `decimal.Decimal` for all money/quantities. |
| Env / deps | `uv` (lockfile `backend/uv.lock`) | `uv sync`, `uv run ...` |
| Web API | FastAPI + Pydantic v2 | JSON API under `/api/v1`. OpenAPI is the contract for the frontend. |
| Persistence | SQLAlchemy 2.0 (typed ORM) + Alembic, SQLite (WAL, FK on) | Postgres-compatible schema; no SQLite-only SQL in app code. |
| PDF text | `pdfplumber` | Page + line-level text with positions. Text-readable PDFs only (PRD R02). |
| Spreadsheets | `openpyxl` (XLSX), stdlib `csv` | |
| Reports | Jinja2 HTML with print CSS | Printable evidence report (R10). |
| Synthetic PDFs | `fpdf2` | Generates clearly-labeled synthetic invoices/tariffs for fixtures/demo. |
| AI (optional) | `anthropic` SDK, model from `TARIFF_AI_MODEL` (default `claude-opus-5-5`) | Disabled when `ANTHROPIC_API_KEY` is unset. Always mocked in tests. |
| Backend QA | pytest, hypothesis, pytest-cov, ruff (lint+format), mypy (strict for `tariff_engine`) | |
| Frontend | Vite 8 + React 19 + TypeScript 5.9 (strict, `noUncheckedIndexedAccess`) | TS pinned to 5.9 for `openapi-typescript` compatibility. |
| Frontend data | TanStack Query v5, `openapi-fetch` + `openapi-typescript` generated types | No hand-written API types. |
| Routing | `react-router` 7.x (pinned major; data router `createBrowserRouter`) | v8 exists but is avoided for API familiarity. |
| Styling | Plain CSS with design tokens (CSS variables) | No CSS framework. |
| Frontend QA | Vitest 5 + Testing Library + jsdom, oxlint (`--deny-warnings`), `tsc -b --noEmit` | |
| E2E | `@playwright/test` 1.64 using preinstalled Chromium (`/opt/pw-browsers/chromium` when present; verified working) | Never run `playwright install` in cloud sessions. |

---

## 2. Repository layout

```
/CLAUDE.md                     agent/developer rules (short)
/Makefile                      canonical commands (see §12)
/docs/PRD.md                   requirements (source of truth)
/docs/ARCHITECTURE.md          this contract
/docs/PLAN.md                  step-by-step execution plan
/docs/requirements.yaml        requirement/acceptance IDs used by pytest markers (traceability)
/docs/TRACEABILITY.md          generated requirement → tests matrix (S24)
/backend/
  pyproject.toml uv.lock alembic.ini
  migrations/                  Alembic
  src/tariff_engine/           PURE deterministic engine (no I/O, no DB, no web, no AI, no clock)
  src/tariff_app/              FastAPI app, persistence, services, extraction, exports, seed
  tests/engine/                engine unit + property tests
  tests/app/                   service + API tests
  tests/acceptance/            PRD §8 acceptance matrix + additional reference cases
  tests/_probes/               (gitignored) scratch tests written by adversarial reviewers
  fixtures/synthetic/          SYNTHETIC tariffs, reference cases, roster, scenario (JSON/CSV)
/frontend/
  package.json vite.config.ts tsconfig*.json .oxlintrc.json playwright.config.ts
  src/api/                     generated `schema.d.ts` + client
  src/app/ src/features/<area>/ src/components/ src/styles/
  e2e/                         Playwright specs
/.github/workflows/ci.yml
```

`tariff_engine` must never import `tariff_app`, SQLAlchemy, FastAPI, pdfplumber, anthropic, `datetime.now`,
`random`, or `float` arithmetic. An engine test enforces this by AST inspection.

---

## 3. Cross-cutting invariants (MUST hold everywhere)

1. **Exact decimals.** Money, rates, quantities, thresholds and tolerances are `Decimal`. Never construct a
   Decimal from a float; never store or transmit them as floats. JSON transports decimals as **strings**
   (`"1234.5600"`). SQLite stores them as TEXT via the `DecimalText` type decorator (SQLite `NUMERIC`
   would silently round-trip through float — forbidden).
2. **Null means missing.** A missing value is `None`/`null`, never `0`. Zero is a real value.
3. **Provenance on every value.** Every input/determinant carries `provenance ∈ {reported, derived, assumed,
   manual}`, a semantic type, a unit, and (when it came from a document) a `source_ref_id`.
   Invoice lines carry `provenance ∈ {extracted, corrected, manual}`.
4. **Immutability + versioning.** Approved tariff versions, completed (reviewed) analyses, assessments,
   reference runs and comparison runs are never mutated. Changes create new versions linked to their
   predecessors. Corrections are append-only with reason and timestamp.
5. **Determinism.** Identical versioned inputs → byte-identical engine results (stable ordering, no clock,
   no randomness, no dict-order dependence, fixed Decimal context).
6. **Separation of concerns the PRD insists on:** calculation coverage vs check scope vs review status vs
   findings (PRD §2 "Readiness"); numerical tolerance vs investigation threshold (R08, §9.2); reported vs
   derived determinants (R05); measured vs billed vs contract demand (R02); public research candidates vs
   confirmed facilities (R01, §9); configuration coverage vs calculation coverage (R01).
7. **AI proposes, user disposes.** AI output (extraction, rule drafts, explanation drafts) is a *proposal*
   stored with status `awaiting_confirmation`; nothing AI-produced changes workflow state, approvals or
   amounts without an explicit user action. Document text is evidence only; instructions inside documents
   are never followed (prompt-injection safe). Every AI-proposed value must cite a source location whose
   text actually contains it, or it is discarded.
8. **Labels preserve scope.** Every result shows: `"Invoice-based check; underlying usage quantities
   unverified"` (R05/§2), demand-determination status, coverage, and any assumptions. Display rounding is
   labeled; threshold logic uses exact stored values.
9. **Single user.** No auth in V1. The workspace user is "Michael Weizman"; finding owner is fixed to him.
10. **Synthetic data is labeled.** Every synthetic tariff, invoice, roster row, reference case and scenario has
    `is_synthetic = true` and visible "SYNTHETIC" labeling in UI and exports. Real public tariff values are
    NOT encoded (sources are unavailable offline); synthetic tariffs only mirror the *patterns* of T1–T5.

---

## 4. Calculation engine (`tariff_engine`) — contract

### 4.1 Decimals and rounding (`decimals.py`)
- `D(x: str | int | Decimal) -> Decimal`; raises `TypeError` for `float` and `bool`.
- Fixed context: `prec=34`, `rounding=ROUND_HALF_EVEN` for intermediate math, traps on `InvalidOperation`,
  `DivisionByZero`, `Overflow`. All engine arithmetic runs inside `engine_context()` (a `localcontext`).
- `RoundingMode` enum: `HALF_UP, HALF_EVEN, DOWN, UP, CEILING, FLOOR` → mapped to `decimal` constants.
- `quantize(value, places: int, mode)`; places may be negative only if explicitly allowed (not needed).

### 4.2 Units (`units.py`)
Dimensional algebra over base symbols: `USD`, `kW`, `h`, `day`, `kV`. A `Unit` is an immutable, canonical,
sorted mapping symbol→exponent; dimensionless is `Unit({})`.
- Parsed aliases (with scale to canonical value):
  `USD`, `$` (1 USD); `cents`, `¢` (0.01 USD); `kW`, `kVA`, `kVAR`, `kvar` (1 kW — same dimension, label
  retained for display; semantic distinction is carried by determinant `semantic_type`, not the unit);
  `MW` (1000 kW); `kWh`, `kVAh`, `kvarh`, `kVARh` (1 kW·h); `MWh` (1000 kW·h); `h`, `hour`, `hours` (1 h);
  `day`, `days`; `kV`; `%`, `percent` (0.01, dimensionless); `1` / `` (dimensionless).
  Compound units with `/` and `*` (e.g. `USD/kWh`, `cents/kWh`, `USD/kW`, `USD/day`, `kWh/kW`).
- `parse_unit(text) -> (Unit, scale: Decimal)`; `Quantity(value: Decimal, unit: Unit, label: str)`.
  Values are normalized to canonical scale on construction from a unit string (e.g. `6.912 cents/kWh` →
  `0.06912 USD/kWh`) while `label` keeps the original text for display/trace.
- `+`, `-`, `min`, `max`, comparisons require identical `Unit` → else `UnitError` naming both units.
  `*`, `/` combine units. Dividing by zero raises `CalcError`.
- A charge component's final unit must be `USD`; a determinant's must equal its declared `output_unit`.

### 4.3 Expression language (`expressions.py`)
Safe formulas parsed with Python `ast` in `mode="eval"` and a strict node whitelist. **Never `eval`.**
- Numbers: literal text is converted with `Decimal(source_segment)` (never via the float `ast.Constant`
  value). Literals are dimensionless; units via `q("200", "h")` / `q(200, "h")`.
- Names resolve to (in order, collisions rejected at validation): built-ins (`billing_month` int 1–12,
  `billing_days` [day], `period_year` int), component keys, parameters, inputs.
- Operators: `+ - * /`, unary `-`, comparisons (`< <= > >= == !=`, chained OK), `and or not`,
  conditional `a if cond else b`. Disallowed: `**`, attributes, subscripts, lambdas, comprehensions,
  f-strings, starred args, keyword args (except none), any call not in the whitelist.
- Functions: `min(a, b, ...)`, `max(a, b, ...)`, `abs(x)`, `round_to(x, places)` (HALF_UP),
  `ceil(x)`, `floor(x)` (dimensionless or same unit), `sqrt(x)` (dimensionless or unit with even exponents),
  `q(number, "unit")`, `in_months(m1, m2, ...)` → bool for `billing_month`.
- API: `parse_expression(src) -> Expr` (raises `ExpressionError` with position), `referenced_names(expr)
  -> frozenset[str]`, `evaluate(expr, env) -> Quantity | bool`. A name bound to a missing input raises
  `MissingInputError(name)`. Source length ≤ 2,000 chars; AST depth ≤ 50.

### 4.4 Rule model (`model.py`, Pydantic v2, frozen; decimals as strings in JSON)
```text
ParameterDef   { name, unit, value: str|None, by_period: {"YYYY-MM": str}|None, description, source_refs: [str] }
               # by_period = monthly adjustment factors (variable riders). value XOR by_period.
DeterminantDef { key, semantic_type, unit, description, shared_compatible: bool, is_series: bool }
Rounding       { places: int, mode: RoundingMode }
Applicability  { months: [1..12]|None, condition: expr|None }   # evaluated against billing month/inputs
Fallback       { input: str, scope_tag: str, label: str }      # e.g. billed_demand_kw when derivation impossible
Calc (discriminated on `type`):
  fixed       { amount: expr }                                    → USD
  rate        { rate: expr, quantity: expr }                      → rate × quantity (unit-checked)
  tiered      { quantity: expr, tiers: [{ size: expr|null, rate: expr }], round_each_tier: Rounding|None }
              # progressive bands; size=null only on the final tier (open-ended); sizes may reference
              # determinants (e.g. q(200,"h") * billing_demand_kw → demand-linked kWh blocks)
  formula     { expr }                                            → any unit (determinants, custom)
  percentage  { base: [component_key], rate: expr(dimensionless) }→ rate × Σ base (explicit base)
  minimum_bill{ floor: expr(USD), against: [component_key] }      → max(0, floor − Σ against)
  lookback    { series: input_key, window_months: int, include_current: bool, months_filter: [int]|None,
                aggregate: "max"|"min", min_points: int, baseline_input: input_key|None }
              # historical maxima/ratchets; baseline_input = documented all-time/firm peak (T2)
ComponentDef   { key, name, role: charge|credit|determinant|subtotal, calc, output_unit,
                 rounding|None, applicability|None, fallback|None, check_scope_tag|None,
                 support_state: supported|awaiting_implementation, invoice_labels: [str],
                 source_refs: [str], depends_on: [component_key], notes }
TariffVersionDef { schedule_code, schedule_kind: base|rider|factor_table|service_rule|amendment,
                 utility_legal_entity, service_territory, version_label, effective_from, effective_to|None,
                 parameters: [ParameterDef], determinants: [DeterminantDef], components: [ComponentDef] }
PackageMember  { version: TariffVersionDef, role: base|rider|factor_table|service_rule|amendment,
                 precedence: int, overrides: [{ component_key, reason }] }
PackageDef     { package_id, members: [PackageMember] }
```
`validate_version(v)` / `validate_package(p)` return a list of `ValidationIssue{severity, code, path, message}`:
unknown names, duplicate keys without an override reason, open tier not last, non-USD charge output, cycles
(with the cycle path), unit errors detectable from declared units, `by_period` keys malformed, roles
inconsistent. A version with `error` issues cannot be approved.

### 4.5 Calculation (`calculator.py`)
```text
InputValue   { key, semantic_type, value: Decimal|None, unit, provenance, source_ref|None, period: "YYYY-MM"|None }
CalcContext  { period_start, period_end, billing_month: "YYYY-MM", account_entity, account_territory,
               inputs: {key: InputValue}, series: {key: {"YYYY-MM": InputValue}} }
calculate(package: PackageDef, ctx: CalcContext, allocation: "none"|"prorate_by_days" = "none") -> CalculationResult
```
Algorithm (all deterministic):
1. **Applicability of sources:** each member version's `utility_legal_entity`/`service_territory` must
   equal the account's → else coverage `blocked` with `wrong_utility_or_territory` (PRD §8 "Dates and
   applicability"). Versions must be effective for the billing period. If a period straddles two versions of
   the same schedule, run once per version and combine each component by day-weight **only** when
   `allocation="prorate_by_days"`; otherwise those components are `blocked` with an input request
   "approve an allocation rule for <schedule> <v1>/<v2>".
2. **Merge** members by precedence; amendment/rider components replace base components only when an
   `override` with a reason exists; otherwise duplicate keys are a validation error.
3. **Graph:** dependencies = referenced names in all expressions + `percentage.base` + `minimum_bill.against`
   + `depends_on`. Topological order with key-sorted tie-breaking. Cycles → validation error.
4. **Evaluate each component** in order:
   - `support_state=awaiting_implementation` → status `unsupported` (counts as uncovered).
   - applicability false → `not_applicable` (value `0 USD`, excluded from uncovered).
   - any dependency not calculated → `blocked` (lists blocking keys).
   - a needed input is null/absent or a `by_period` factor is missing for the billing month, or lookback has
     < `min_points` → `missing_input` with specific `InputRequest{key, semantic_type, period|None,
     description, needed_by: [component_key], document_hint}`.
     If the component has a `fallback` and that input is present → status `calculated`, `fallback_used=true`,
     value = fallback input; adds check-scope note (e.g. demand determination **unchecked**).
   - arithmetic/unit failure → `error` with message.
   - otherwise `calculated`: compute raw value, apply `rounding` (if any) → value. Keep `raw_value`.
   Each result records a `trace` (ordered steps: expression text, substituted values with units, tier
   allocations with per-tier quantity/rate/subtotal, lookback points considered), `used_inputs` with
   provenance and source refs, `used_parameters`, `source_refs`, `schedule_code`, `version_label`.
5. **Coverage:** consider applicable components with role `charge|credit`. `complete` = all `calculated`;
   `blocked` = none calculated or a structural block; else `partial`. `expected_total` = Σ charge/credit
   values **only if complete**; `supported_subtotal` = Σ calculated charge/credit values always;
   `uncovered` = keys not calculated (with status and reason).
6. **Check scope:** `{ notice: "Invoice-based check; underlying usage quantities unverified",
   demand_determination: checked|unchecked|not_applicable, notes: [...] }`. `unchecked` when any component
   tagged `check_scope_tag="demand_determination"` used its fallback (billed demand); `checked` when such
   components were derived from measured demand + sufficient history.
7. `CalculationResult { engine_version, billing_month, components: [ComponentResult], coverage,
   expected_total|None, supported_subtotal, uncovered, input_requests (deduped, sorted), check_scope,
   blocking_issues }` — fully JSON-serializable (`model_dump(mode="json")`), stable ordering.

### 4.6 Reconciliation & assessment (`reconcile.py`)
```text
InvoiceLineIn { id, label, amount: Decimal|None, category: current_charge|balance_item|informational,
                balance_type: prior_balance|payment|balance_credit|late_charge|other|None,
                quantity|None, quantity_unit|None, rate|None, rate_unit|None, source_ref|None,
                out_of_scope_reason|None }
MappingGroup  { group_key, line_ids: [str], component_keys: [str] }   # supports one-to-many / many-to-one
reconcile(calc, lines, groups, stated_amount_due|None) -> Reconciliation
assess(reconciliation, threshold: Decimal) -> AssessmentResult
```
- For each group: `billed = Σ line amounts` (None if any is None), `expected = Σ component values` (None
  if any mapped component is not calculated/not applicable-with-value), `difference = billed − expected`
  (signed; R06), `pct = difference / |expected|` when `expected ≠ 0`, else `None` (no denominator).
- Row statuses: `compared`, `expected_missing` (coverage gap — never treated as zero), `billed_missing`,
  `unmapped_line` (current charge with no component and no out-of-scope reason), `not_billed` (calculated
  non-zero component with no line → billed treated as 0 **only** for components with an explicit mapping
  absence; reported as its own status), `out_of_scope` (line explicitly classified, with reason).
- **Flag rule (exact Decimals, no rounding):** `expected == 0 and billed == 0` → matched;
  `expected == 0 and billed ≠ 0` → flagged (`requires_review_zero_expected`); else flagged iff
  `|pct| > threshold` (strictly greater: exactly 1% is within threshold). Both directions. Rows without an
  expected value are **not** flagged but listed as coverage gaps.
- **Totals:** `comparable_billed_total` = Σ current_charge lines (in scope); `expected_total` from calc only if
  coverage complete **and** no unmapped current-charge lines; total flagged by the same rule. Partial →
  show `supported_subtotal` and the billed amount of covered groups, never a full expected total.
- **Balance bridge** (separate from comparable charges): prior balance, payments, balance credits, late
  charges, other → `computed_amount_due = Σ balance items + comparable_billed_total`; compare to stated
  amount due; report `bridge_difference`.
- Offsetting differences are always shown per row even when the net is zero (PRD §8 "Comparison and
  offsets").
- `classify_reference(calculated, reference, abs_tol|None, rel_tol|None) -> pass|fail|pending` (PRD §9.2):
  pass iff `|calc − ref| ≤ max(abs_tol, rel_tol × |ref|)`, omitting unset terms; at `ref == 0` use only the
  absolute term; when the applicable term(s) are unset → `pending` ("Reference validation pending").

### 4.7 Explanations (`explain.py`)
Deterministic, evidence-backed explanation generators run over reconciliation rows. Each explanation:
`{ group_key, kind, text, status: documented|awaiting_confirmation|unresolved, evidence: [EvidenceLink{type:
rule|parameter|invoice_line|input|document, ref, label}] }`. **No evidence → no explanation** (unsupported
causal claims are omitted, R08). Generators (only emit when the numbers prove it):
`rate_mismatch` (line rate × line qty = billed and differs from approved rate), `quantity_mismatch` (line qty ≠
determinant used), `rounding_only` (|diff| ≤ $0.01 × number of rounded components), `not_billed`,
`unmapped_line`, `fallback_billed_demand` (charges checked at billed demand; demand determination unchecked),
`ratchet_applied` (billing demand set by lookback/contract floor, with the binding term), `minimum_bill_applied`,
`missing_factor` (variable rider factor absent). Anything else → `unresolved` with "No documented cause".

### 4.8 Comparison (`comparison.py`)
```text
CandidateIn { candidate_id, label, package: PackageDef, eligibility_status: unconfirmed|eligible|ineligible,
              closed_to_new_business: bool, existing_eligibility_documented: bool,
              reference_status: passed|failed|pending|none, unmodeled_conditions: [str],
              specific_inputs: {"YYYY-MM": {key: InputValue}} }
ScenarioIn  { months: ["YYYY-MM"...], shared_inputs: {"YYYY-MM": {key: InputValue}},
              single_month_reuse_approved: bool, account_entity/territory per candidate }
compare(scenario, candidates, baseline_id|None) -> ComparisonResult
```
- Per candidate, per month: build a `CalcContext`; a shared input fills a candidate determinant **only** if
  that `DeterminantDef.shared_compatible` is true and `semantic_type`+unit match; TOU and other
  candidate-defined determinants require candidate-specific inputs (a flat monthly total never supplies
  on-peak quantities — PRD R12).
- Missing months without approved single-month reuse → partial. Reuse of one month for an annual horizon
  only when `single_month_reuse_approved` (visible assumption).
- `modeled = all months complete`. **Rankable** iff modeled ∧ eligibility `eligible` ∧ (not closed-to-new or
  existing eligibility documented) ∧ reference_status `passed`. Exclusion reasons are listed per candidate.
- Rank rankable candidates by total ascending (ties by label); `baseline_diff = total − baseline_total`.
- `cheapest_label`: `"Lowest modeled cost for scenario '<name>' over <first>–<last> (<n> months) among <k>
  eligible, fully modeled candidates: <label>"` (names scenario, horizon and candidate set — R12).
- `is_scenario_estimate = true` when any value used has provenance `assumed` (label "Scenario estimate").

### 4.9 Engine version
`tariff_engine.ENGINE_VERSION` (semver string). Recorded on every analysis, reference run, comparison run.
Any change to engine arithmetic semantics bumps it.

---

## 5. Persistence model (`tariff_app.models`)

IDs are opaque prefixed strings `"<prefix>_<16 hex>"` (`fac_`, `acct_`, `doc_`, `src_`, `tsch_`, `tver_`,
`tpkg_`, `asg_`, `inv_`, `line_`, `iset_`, `ival_`, `an_`, `asm_`, `fnd_`, `ref_`, `scn_`, `cand_`, `run_`…).
Analyses also carry a human code `AN-000001`. Timestamps are timezone-aware UTC via an injectable `Clock`.
All decimal columns use `DecimalText`. JSON columns hold engine models dumped with `mode="json"`.

| Table | Key columns (beyond id/created_at) |
|---|---|
| `workspace` | name, user_display_name (`Michael Weizman`) |
| `settings_version` | version_no (unique), investigation_threshold (default `0.01`), abs_tolerance NULL, rel_tolerance NULL, reason |
| `tolerance_override` | settings_version_id, component_key, abs_tolerance NULL, rel_tolerance NULL |
| `facility` | code (unique), name, service_address, state, operating_status (`operating/planned/unknown`), record_status (`confirmed/research_candidate`), research_evidence, research_source_refs (json), is_synthetic, notes, updated_at |
| `production_unit` | facility_id, name, description |
| `utility_account` | facility_id, production_unit_id NULL, account_number, meter_ids (json), utility_legal_entity NULL, service_territory NULL, tariff_code NULL, invoice_issuer NULL, billing_arrangement (`direct_utility/host_site/supplier_contract/unknown`), scope_classification (`regulated_in_scope/host_site/supplier_contract/pending`), service_voltage_kv NULL, record_status (`confirmed/pending_confirmation`), is_synthetic, notes |
| `account_fact` | account_id, key, semantic_type, value NULL, text_value NULL, unit, effective_from, effective_to NULL, provenance, source_ref_id NULL, note, superseded_by_id NULL |
| `source_document` | kind (`invoice/tariff/rider/factor_table/contract/roster/billing_summary/credit_evidence/other`), original_filename, content_hash (unique), storage_path, mime_type, size_bytes, source_url NULL, publication_date NULL, effective_date NULL, retrieved_at NULL, uploaded_at, facility_id NULL, account_id NULL, page_count, extraction_status (`pending/extracted/partial_unreadable/failed/not_applicable`), is_synthetic |
| `document_page` | document_id, page_no, text, readable, lines (json: [{line_no, text, x0, top}]) |
| `source_ref` | document_id, page_no NULL, locator, quote — quote must occur (whitespace-normalized) in the page text |
| `tariff_schedule` | code, name, kind, utility_legal_entity, service_territory, status (`open/closed_to_new_business/withdrawn`), is_private, account_id NULL, is_synthetic, description |
| `tariff_version` | schedule_id, version_no, version_label, effective_from, effective_to NULL, source_document_id NULL, definition (json TariffVersionDef), content_hash, state (`draft/approved/superseded/rejected`), based_on_version_id NULL, edit_reason, approved_at NULL, approval_reason NULL, reference_status (`none/pending/passed/failed`) |
| `tariff_package` / `tariff_package_member` | name, entity, territory / package_id, schedule_id, role, precedence, overrides (json) |
| `tariff_assignment` | account_id, package_id, effective_from, effective_to NULL, state (`proposed/approved/retired`), approved_at, reason, allocation_rule (`none/prorate_by_days`), allocation_approved_at NULL |
| `line_mapping_rule` | account_id, label_pattern, match_type (`exact/contains/regex`), component_keys (json), category, balance_type NULL, state (`proposed/approved`), reason |
| `invoice` | account_id NULL, document_id NULL, invoice_number, statement_date, period_start, period_end, billing_month, amount_due NULL, version_no, supersedes_id NULL, superseded_by_id NULL, state (`pending_confirmation/confirmed/superseded/rejected_duplicate`), assignment_status (`assigned/ambiguous/unassigned`), extraction_method (`heuristic/ai/manual`), is_synthetic |
| `invoice_line` | invoice_id, line_no, label, category, balance_type NULL, amount NULL, quantity NULL, quantity_unit, rate NULL, rate_unit, provenance, source_ref_id NULL, out_of_scope_reason NULL |
| `invoice_line_mapping` | invoice_id, group_key, invoice_line_id NULL, component_key NULL, origin (`rule/manual`), reviewed |
| `input_set` / `input_value` | account_id, invoice_id NULL, billing_month, period_start/end, version_no, supersedes_id NULL, reason / input_set_id, key, semantic_type, value NULL, unit, period NULL, provenance, source_ref_id NULL, note |
| `correction_event` | entity_type, entity_id, field, old_value, new_value, reason |
| `analysis` | code, account_id, invoice_id, input_set_id, assignment_id, billing_month, version_no, supersedes_id NULL, superseded_by_id NULL, status (`draft/ready_for_review/reviewed`), is_stale, stale_reasons (json), engine_version, tariff_version_ids (json), fingerprint, snapshot (json), result (json: calculation + reconciliation + explanations), coverage, demand_determination, partial_acknowledged, partial_ack_note, review_conclusion NULL, review_notes NULL, reviewed_at NULL |
| `assessment` | analysis_id, settings_version_id, version_no, threshold, abs/rel tolerance snapshot, rows (json), total_flagged, flagged_count |
| `explanation_review` | analysis_id, group_key, explanation_index NULL, status, note, origin (`engine/ai/user`), text NULL, evidence (json) |
| `finding` (+ `finding_event`) | account_id, analysis_id NULL, invoice_id NULL, billing_month, group_key NULL, component_key NULL, kind (`difference/missing_item/coverage_gap`), amount NULL, state (`needs_review/explained/accepted_below_threshold/utility_inquiry/credit_requested/closed`), classification (`potential_discrepancy/confirmed_billing_error/not_an_error`), notes, next_action, evidence (json), owner (fixed) |
| `billing_error_confirmation` | finding_id, confirmed_on, utility_reference, amount, evidence_document_id NULL, note |
| `credit_receipt` | finding_id NOT NULL, document_id NOT NULL, amount, received_on, applied_invoice_id NULL, note |
| `reference_case` / `reference_run` | name, account_id NULL, package_id, is_synthetic, held_out, context (json), expected (json {component_key: decimal}), expected_scope, source_version_ids, from_analysis_id NULL, version_no / case_id, settings_version_id, engine_version, results (json), overall (`passed/failed/pending`) |
| `comparison_scenario` / `comparison_candidate` / `scenario_input` / `comparison_run` | facility_id, name, version_no, supersedes_id, horizon_start, horizon_months, baseline_candidate_id, single_month_reuse_approved, forecast_note, change_note / scenario_id, package_id, label, eligibility_status, eligibility_evidence, evidence_document_ids, existing_eligibility_documented, grandfathering_note, commitments, unmodeled_conditions, confirmed / scenario_id, candidate_id NULL (shared), billing_month, key, semantic_type, value NULL, unit, provenance, source_ref_id, note / scenario_id, scenario_version_no, engine_version, result (json) |

Schema changes go through Alembic; `tests/app/test_migrations.py` asserts `alembic upgrade head` produces
exactly the ORM metadata (no drift).

---

## 6. Versioning & lifecycle semantics (R04, §5 "Review states", §7 "Processing sequence")

- **Tariff versions:** `draft → approved` (Michael, with reason + timestamp) → `superseded` when a newer
  approved version covers the same effective interval. Editing an approved version creates a **new draft**
  (`based_on_version_id`), never mutates. Approval requires zero validation errors and an approved account
  applicability (`tariff_assignment.state=approved`) before any analysis using it can be finalized.
- **Staleness:** approving a new tariff version, correcting an invoice line, creating a new input-set
  version, replacing a source document, or changing a tariff assignment marks every **draft /
  ready_for_review** analysis that used the old object `is_stale=true` with a reason. Reviewed analyses are
  never modified; the API reports "newer inputs/rules exist" for them as a computed flag.
  `GET /tariff-versions/{id}/affected-analyses` lists analyses to rerun explicitly.
- **Analysis versions:** every calculation creates an analysis row. A rerun creates a new version with
  `supersedes_id` and leaves the prior row intact. Creation is idempotent: the same
  `fingerprint = sha256(invoice version, input-set version, tariff version ids, assignment, mappings, engine
  version)` returns the existing non-superseded analysis instead of duplicating (safe retries).
- **Analysis states:** `draft → ready_for_review` (requires saved calculation; if coverage ≠ complete,
  requires `partial_acknowledged` with note) `→ reviewed` (requires conclusion; freezes analysis, explanation
  reviews and the latest assessment). Findings remain independently open.
- **Assessments:** created at analysis creation using current settings. A settings change never mutates an
  assessment; an explicit `POST /analyses/{id}/reevaluate` creates a new assessment version reusing the
  saved amounts. Reports/exports reference a specific assessment version.
- **Settings:** append-only versions. Current = highest version_no.
- **Invoices:** identical content hash → duplicate (rejected, points to original). Same account + invoice
  number/period with different content → *revision candidate*: requires explicit `supersede` action; the
  new invoice gets `version_no+1`, the prior becomes `superseded`. Analyses of superseded invoices are
  retained and labeled.
- **Comparison scenarios:** editing creates a new scenario version; `GET /comparison-scenarios/{id}/diff/{other}`
  shows changed assumptions. Runs are immutable and record the scenario version.

---

## 7. Extraction (`tariff_app.extraction`)

- `pdf_text.extract(path) -> [PageText{page_no, text, lines[{line_no, text, x0, top}], readable}]`.
  A page is unreadable when it has < 20 non-whitespace characters → input request "replace page or
  transcribe manually" (R02).
- `InvoiceExtractor` protocol → `InvoiceProposal{ fields (invoice_number, account_number, statement_date,
  period_start, period_end, amount_due) each with value + source locator, lines [...], determinants
  [{key, semantic_type, value, unit, period, locator}], warnings }`.
  - `HeuristicInvoiceExtractor`: deterministic regex parser for the synthetic invoice layout and common
    "Label  qty unit @ rate  $amount" lines. Always available.
  - `ClaudeInvoiceExtractor`: structured-output call (JSON schema) when `ANTHROPIC_API_KEY` is set. Every
    returned value must be found in the cited page/line text (normalized); otherwise dropped with a warning.
- Tariff rule drafting from documents (optional AI) produces a **draft** `TariffVersionDef` with
  `source_refs` validated the same way; never auto-approved.
- Proposals are persisted as `pending_confirmation`; `POST .../confirm` (with optional corrections) creates
  the invoice/lines/input set. Every corrected field records a `correction_event`.

---

## 8. API conventions (`/api/v1`)

- Resource routers per area: `settings`, `facilities`, `accounts`, `roster`, `documents`, `source-refs`,
  `tariff-schedules`, `tariff-versions`, `tariff-packages`, `assignments`, `mapping-rules`, `invoices`,
  `input-sets`, `analyses`, `assessments`, `findings`, `reference-cases`, `comparison-scenarios`,
  `portfolio`, `exports`, `meta`.
- Pydantic response models for everything (documented in OpenAPI). Decimals are strings. Enums are strings.
- Errors: HTTP 4xx with `{"detail": str, "code": str, "context": {...}}`; codes are stable identifiers
  (e.g. `duplicate_invoice`, `invalid_state_transition`, `validation_failed`, `stale_analysis`).
- State transitions are explicit POST actions (`/approve`, `/submit-for-review`, `/review`, `/supersede`,
  `/reevaluate`, `/rerun`), never implicit side-effects of PATCH.
- `GET /api/v1/meta` → app version, engine version, AI availability, synthetic-data flag.
- `make openapi` writes `frontend/src/api/openapi.json` and regenerates `frontend/src/api/schema.d.ts`;
  CI fails if they are out of date.

---

## 9. Frontend structure

Routes: `/` Portfolio queue · `/facilities`, `/facilities/:id` · `/accounts/:id` · `/roster/import` ·
`/documents`, `/documents/:id` · `/invoices/:id` (intake/confirm/correct) · `/tariffs`, `/tariffs/:scheduleId`,
`/tariff-versions/:id` · `/analyses/:id` (main review screen) · `/findings`, `/findings/:id` ·
`/comparisons`, `/comparisons/:id` · `/reference-cases` · `/settings`.

The **review screen** must answer PRD §5's five questions in visible regions: *What am I reviewing?* (header),
*How complete is this?* (coverage panel), *What should we pay?* (totals + balance bridge), *Why is it
different?* (component table + evidence drawer with rule, exact passage, calculation trace, invoice line
source), *What happens next?* (input requests, decision, findings, export).

Shared components: `Money` (exact string input; displays rounded with tooltip of exact value + "rounded
display" label), `Variance` (signed diff, pct, flag icon), `CoverageBadge`, `ProvenanceTag`, `StatusPill`,
`SourcePassage`, `SyntheticBadge`, `VersionBanner`. The frontend never does financial arithmetic; it displays
server-computed values.

---

## 10. Testing conventions

- Every behavior ships with tests in the same step. `make check` must pass before a step is committed.
- Pytest marker `@pytest.mark.req("R06", "AT-THRESHOLD")` links tests to IDs in `docs/requirements.yaml`; S24
  adds a meta-test that every ID is covered and generates `docs/TRACEABILITY.md`.
- Engine: example tests + Hypothesis property tests (tier allocation conservation, determinism, threshold
  symmetry, unit algebra laws).
- App: service tests on a temp SQLite DB + temp data dir; API tests via `TestClient`; no network; AI client
  injected and mocked.
- Acceptance: one module per PRD §8 matrix row + additional reference cases, using synthetic fixtures with
  hand-computed expected values documented inline.
- Frontend: Vitest + Testing Library for components/screens with mocked API; Playwright E2E for the PRD §10.1
  demo sequence on seeded synthetic data.

---

## 11. Configuration

`tariff_app.config.Settings` (pydantic-settings, prefix `TARIFF_`): `DATA_DIR` (default `backend/.data`),
`DATABASE_URL` (default `sqlite:///<DATA_DIR>/tariff.sqlite3`), `AI_MODEL` (default `claude-opus-5-5`),
`MAX_UPLOAD_MB` (default 25), `SERVE_FRONTEND_DIST` (path or empty). `ANTHROPIC_API_KEY` read from env.

---

## 12. Canonical commands (Makefile)

`make setup` · `make check` (lint + typecheck + all unit tests, backend and frontend) · `make backend-test` ·
`make frontend-test` · `make lint` · `make fmt` · `make dev` (API :8000 + Vite :5173) · `make seed`
(synthetic demo workspace) · `make openapi` · `make e2e` · `make run` (single process serving built SPA).
