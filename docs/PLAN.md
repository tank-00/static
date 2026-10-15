# Utility Tariff Interpreter — MVP Execution Plan (v2)

Source of truth: `docs/PRD.md`. Technical contract: `docs/ARCHITECTURE.md` (cited as `A§n`). Each step ships
code + tests, passes `make check`, is reviewed, and is committed before the next step starts. v2 splits large
steps and adds the cross-step contracts surfaced by adversarial plan review.

---

## A. Step gate (applies to every step)

1. **Implement**: read the step spec, cited PRD sections and A§ sections; build on existing code. Write tests
   for every bullet, with hand-derived expected values, and run `make check` until it is green. Implement only this
   step; a stub is allowed only where a later step is named. If a contract detail must change, update
   `docs/ARCHITECTURE.md` in the same change and report it as a deviation.
2. **Verify** (two independent agents in parallel):
   - *Spec reviewer*: checks the diff against the spec, PRD and A§3 invariants, and runs `make check`.
   - *Adversarial prober*: writes probes in `backend/tests/_probes/` or `frontend/src/__probes__/` that target
     boundaries, null vs zero, units, signs, ordering/determinism, immutability, idempotency, invalid
     transitions and malformed input.
3. **Fix**: fix every blocker and major (and cheap minors), promote useful probes into the real suite, and re-run
   `make check`. Repeat verify → fix for up to 3 rounds.
4. **Commit**: clear the probe directories, confirm `make check` is green, then
   `git commit -m "Sxx: <title>"` with the session trailers. Each phase is pushed when it ends.

**DoD:** every bullet has tests carrying `req` markers where applicable; `make check` is green within 180 s;
there are no TODOs for in-scope behavior; and lockfiles changed only when the step adds a dependency.

## B. Phases

```
P0  S00
P1  S01 S02 S03 S04 S05 S06 S07a S07b S08 S09                      pure engine
P2  S10 S11 S12 S13 S14a S14b S15a S15b S15c S15d S15e S16a S16b S17 S18 S19 S20a S20b S21 S22 S23a S23b S24a S24b
P3  S25 S26 S27a S27b S28a S28b S29a S29b S30 S31 S32               frontend on the stable API + seed
P4  S33 S34                                                          E2E demo, final audit
```

---

## C. Steps

### P0 — Environment

#### S00. Environment & scaffold *(done)*
- Backend: uv project, FastAPI meta endpoint, ruff/mypy/pytest. Frontend: Vite React TS with Vitest and oxlint
  (`--deny-warnings`). Playwright uses the preinstalled Chromium.
- Makefile, CI, SessionStart hook, `CLAUDE.md`, docs, and `requirements.yaml`. Probe directories are isolated.
- Follow-up chores (done with plan v2): add `pytest-xdist`, `pyyaml`, the Hypothesis profile and
  `tests/strategies.py`; switch CI/setup to `uv sync --locked` + `npm ci`; make the hook run `npm ci` only when
  `node_modules` is stale; run `backend-test` as `pytest -n 4 --dist loadfile --durations=10`; make
  `openapi-check` non-mutating.

### P1 — Deterministic engine (`backend/src/tariff_engine`)

#### S01. Dates, decimals, canonical JSON, units — A§4.0–4.2, A§3.1/3.3
- `dates.py`: billing_days under both conventions, month arithmetic, `month_days`.
- `decimals.py`: `D()`, `engine_context`, `RoundingMode`, `quantize`, `canonical_str`, `DecimalStr`,
  `canonical_json`.
- `units.py`: Unit, `parse_unit`, Quantity, aliases (incl. mills, kVAR/kVA as distinct dimensions), and the
  unit-polymorphic zero rule.
- Engine purity test: an AST scan that forbids float, `datetime.now`, random, uuid, time, and app/DB/web imports.
- **Tests:**
  - Float/bool rejected by `D()` and by `DecimalStr` (JSON number → error).
  - `canonical_str(Decimal("1E+3")) == "1000"`; `-0.00` → `"0.00"`.
  - `canonical_json` sorts keys and is byte-stable.
  - Every rounding mode, including negatives and .5 ties.
  - Units:
    - `6.912 cents/kWh × 1000 kWh = 69.12 USD`, and `mills` scale.
    - kW + kWh → UnitError; kW + kVAR → UnitError; USD/kW × kVAR is not USD.
    - `kWh/kW = h`; `q(200,h) × 1500 kW = 300000 kWh`.
  - Dates: billing_days inclusive vs read_to_read; `month_add("2026-07", -11) == "2025-08"`.
  - Hypothesis (bounded strategies): `*` commutative/associative over units; `(a+b)−b == a` within bounds.

#### S02. Expression language — A§4.3, R03
- Parser with whitelist, `referenced_names`, and `evaluate` over Quantities. Functions: `round_to` with mode,
  `power_factor`, `apparent`, `as_reactive_equiv`, `in_months`, `q`. Built-ins: `billing_days`,
  `days_in_billing_month`. Errors carry positions; size and depth limits.
- **Tests:**
  - Literal precision: `0.1+0.2 == 0.3` exactly.
  - Expressions: `max(demand_measured_kw, 0.95 * x)`; ternary + `in_months`; `max(x_kW, 0)` (polymorphic zero).
  - `round_to(-1.235, 2, "DOWN") == -1.23`; `power_factor(q(900,"kW"), q(436,"kVAR"))` hand-computed;
    excess kVAR `max(kvar − as_reactive_equiv(kw)/3, 0)`.
  - Rejected constructs: `__import__`, attribute, `**`, subscript, lambda, comprehension, kwargs, unknown
    function, >2,000 chars, depth > 50.
  - Errors: division by zero → CalcError; missing input → MissingInputError naming the key.
  - `referenced_names` excludes functions and built-ins.

#### S03. Catalog, rule model, validation, package merge & graph — A§4.4–4.6, R03, R04, §9.1
- `catalog.py` holds the A§4.4 table. `model.py` (A§4.5) round-trips JSON with `DecimalStr` and exports a JSON
  schema.
- `package.py`: name resolution (version-scoped vs package-scoped parameters), external_refs, overrides with
  reasons (higher precedence wins), selector resolution, collision rules.
- `graph.py`: deterministic topological order and cycle path.
- `validate_version` / `validate_package` cover every A§4.5 issue code.
- **Tests:**
  - One minimal bad fixture per issue code.
  - A rider with a percentage base on base keys validates when it declares externals and fails without them;
    the package resolves it.
  - Base and rider both define version-scoped `rate` → no collision.
  - An amendment parameter override with a reason is applied and traced; equal-precedence collision → error.
  - Cycle path reported. Unknown semantic type / catalog mismatch / TOU without definition_id → error.
  - The selector `{roles:[charge], exclude:[...]}` resolves to a sorted list.

#### S04. Primitive evaluators — A§4.5 calc types, A§4.7 step 4, §9.1
- Evaluators: `fixed, rate, tiered` (bands + subtiers, `round_each_tier`), `formula, percentage, minimum_bill`,
  `lookback` (windows, filter, aggregates, completeness, baseline, series_expr).
- Also: applicability; rounding with downstream use of rounded values; component-level fallback; `by_period`
  resolution; credit-sign check; traces; used inputs/parameters.
- **Tests** (all hand-computed):
  - Rates: cents vs dollars.
  - Nested HUD blocks:
    - BD=1,500 kW, 700,000 kWh → band1 300,000 (sub 3,000/7,000/190,000/100,000), band2 300,000,
      band3 100,000, band4 0.
    - BD=10 kW → band1 2,000.
  - Billing demand rounded to whole kW feeds band sizes.
  - Percentage base including a negative credit; minimum bill binding / non-binding / binding because of a
    credit.
  - Lookback:
    - Month 2026-07, months_back 11, filter 6–9 → required {2025-08, 2025-09, 2026-06}; month 2026-10 →
      {2026-06…2026-09}.
    - One missing month → missing_input naming it. since_baseline with as-of 2024-03.
    - Mean aggregate; series_expr (PF-adjusted); series_expr referencing a component → error.
  - Missing `by_period` month → factor request; unresolved parameter → missing_source.
  - Credit evaluating > 0 → error `credit_sign`.
  - Hypothesis: tier allocation conserves quantity and is monotone.

#### S05. Calculator orchestration, coverage & check scope — A§4.7, R05, §8 (missing rider/history, dates/applicability, provenance)
- Source applicability gate; member windows; version selection per effective_basis; segments + prorate;
  missing_source.
- Merge; topological evaluation with A§4.7 4(c)–(d) fallback semantics (non-blocking requests).
- Coverage, totals, check scope, assumptions, deduped requests, resolved_versions.
- **Tests:**
  - Complete case. Missing fuel-rider member/version → partial, supported subtotal excludes the rider, request
    names the rider code + month. Missing history → one request per month.
  - Status handling: dependency blocked → propagates; awaiting_implementation → unsupported;
    awaiting_input → missing_source.
  - Wrong entity/territory → blocked.
  - Effective dates:
    - 2026-05-15…2026-06-14 vs a change on 2026-06-01: weights 17/31 and 14/31 under prorate; blocked with an
      allocation request under none.
    - billing_month basis → single version, no request. Base and rider changing on different dates → 3
      segments.
  - Demand determination:
    - Measured + billed present, 3/11 history months → billing demand = billed, `unchecked`, history requests
      non-blocking.
    - Measured + full history → `checked`. Neither → missing_input.
  - Determinism: shuffled members/components/inputs → identical canonical JSON.
  - Reported vs derived provenance retained.

#### S06. Reconciliation, assessment & reference classification — A§4.8, R06, R07, §8 (threshold, zero/missing), §9.2
- **Tests:**
  - PRD boundaries at row and total level, both directions, signed differences: expected 100000 vs billed
    101000 (not flagged), 101001 (flagged), 98999 (flagged).
  - Credits: −100 vs −101 (not flagged), vs −101.01 (flagged).
  - Zero and missing:
    - 0/0 matched; 0/nonzero flagged.
    - Missing expected → coverage gap, not zero; missing billed → billed_missing.
  - Not-applicable / not-billed rows:
    - Summer-only component not_applicable in January mapped to a $500 line → flagged.
    - Calculated $120 rider with no line → not_billed, −120.00, flagged. A zero minimum-bill top-up with no
      line → no row.
  - Mapping rules:
    - Summer + winter components on one line. Double mapping → error.
    - Unmapped $50 line → reconciliation partial, no full total. Unreviewed group → partial.
    - Coarse-group warning. group_key stable across a revised invoice.
  - Subtotals: energy + fuel group (fuel missing) → covered subtotals consistent.
  - Bridge:
    - A $35 out-of-scope tax line and a −$200 balance credit → bridge difference 0.
    - prior_period_adjustment; a null amount → incomplete.
  - Offsetting +X/−X rows visible with zero net.
  - Tolerances:
    - USD pass/fail/pending; ref=0 uses only the absolute term.
    - 1 kW determinant mismatch fails despite $5 tolerance; per-field override inheritance; overall roll-up.
  - Hypothesis: flag symmetric in sign; cross-multiplication equals division whenever division is exact.

#### S07a. Synthetic fixtures I + golden reference cases — A§11, PRD §8 additional cases, §9 T1–T3
- Tariff fixtures (A§11 format) with separate rider/factor_table schedules and packages:
  - `SYN-GA-PLL`: nested HUD blocks, 95% summer ratchet on billed demand, contract floor, minimum bill, excess-kVAR
    charge, %-of-base cost-recovery riders as separate rider schedules.
  - `SYN-ELL-LIPS`: demand tiers, energy, reactive, monthly fuel factor_table, facilities/loss terms, contract
    minimum, all-time-peak baseline floor; status closed_to_new_business.
  - `SYN-ELL-LPS`: seasonal demand, fuel, voltage credit, transformer losses, PF adjustment, off-peak
    adjustment, contract floor.
- Reference cases include RC seasonal demand, demand block, minimum bill, voltage/loss, historical ratchet,
  variable rider and negative credit, plus one held-out month per package. Numbers are small and round.
- An independent derivation evaluator (Decimal ops only) re-checks every `derivation`.
- **Tests:**
  - Every fixture validates; every ref_key is declared and every quote is on its page.
  - Golden cases equal their expected values exactly. Derivations recompute.
  - Catalog meta-test over fixtures.

#### S07b. Synthetic fixtures II + Florida comparison set — A§11, §9 T4–T5, D07
- `SYN-FPL-GSLDT`: governing sheet + separate summary document. Fixed, demand, TOU energy with definition_ids,
  fuel/conservation/capacity/environmental/storm riders; curtailment credit `awaiting_implementation`.
- `SYN-GA-TOU-HLF`: TOU + eligibility conditions.
- Six Florida candidates `SYN-FL-C1..C6`: one closed_to_new_business, one ineligible new enrollment, one TOU with
  a different window definition, one ratchet needing history.
- `scenarios/fl_planned_2028.json`: 12 months (assumed inputs) plus reference cases per candidate package.
- **Tests:** same as S07a; scenario fixture keys are in the catalog; every candidate package validates.

#### S08. Explanations — A§4.9, R08
- **Tests:**
  - Each generator fires only on proving data, with evidence and the right status.
  - rate_mismatch is suppressed on coarse groups; a combined rate + quantity difference does not yield a false
    single cause.
  - Unresolved → "No documented cause" with an invoice-line evidence link.
  - ratchet_applied names the binding term. Deterministic ordering.

#### S09. Comparison engine — A§4.10, R11, R12, §6
- `derive_eligibility` + `compare`.
- **Tests:**
  - A six-candidate, 12-month scenario.
  - Shared vs specific inputs: definition_id mismatch → partial with a candidate-specific request; a flat total
    never fills on-peak.
  - Eligibility:
    - Grandfathered closed tariff with documented evidence and permitted use ranks; without evidence → excluded.
    - Open conflict, unknown condition and site territory mismatch → each excluded.
  - Every exclusion code.
  - Ratchet candidate complete via history_inputs; partial without, naming the months. Chained
    publishes_series within the horizon.
  - Assumed future factor → scenario estimate. single_month_reuse only with approval.
  - Baseline diff and its reasons; cheapest label wording incl. ties and k=0; cost drivers.

### P2 — Persistence, services & API (`backend/src/tariff_app`)

#### S10. DB & app foundation — A§5 (preamble), A§8 errors, A§10, A§12
- Config, SQLAlchemy engine (connect event: `foreign_keys=ON`, WAL, `busy_timeout`, `check_same_thread=False`),
  `DecimalText`, `UTCDateTime`, ids, Clock, JSON column with canonical serializer and float-rejecting
  deserializer, content-addressed file store, event bus.
- App factory with DI, lifespan `alembic upgrade head`, error envelope + OpenAPI cleanup, `ErrorOut`.
- Alembic env (A§5 settings); tables `workspace`, `settings_version`, `tolerance_override` (revision
  `s10_foundation`).
- Test harness: session template DB copied per test, `db_session`/`client` fixtures, `factories.py` skeleton, and
  a conftest hook rejecting unknown `req` IDs.
- **Tests:**
  - Decimal round-trip exactness: `0.1`, `123456789012345678.123456789`, `-0`. Null vs zero; JSON columns never
    yield float.
  - FK enforced. Drift test. File store dedupe.
  - Error envelopes: AppError, HTTPException, validation error; the OpenAPI 422 → ErrorOut; a JSON number for a
    decimal → 422 `decimal_must_be_string`.

#### S11. Settings — R07, §7 workspace/settings, §9.2, D08
- Service + API: current, history, update with reason → new version, overrides with unit.
- **Tests:** defaults (0.01, tolerances unset); validation (0 ≤ threshold ≤ 1, tolerances ≥ 0); history; current =
  latest; overrides; previous versions unchanged.

#### S12. Facilities, accounts, facts, research candidates, roster import — R01, §9 coverage, App. A, D01
- CRUD for facilities (archive, not delete), production units, accounts, and account facts (provenance,
  effective dates, superseding). Correction events for entity/territory/tariff_code.
- Research candidates: separate table seeded with the 13 Appendix A locations (LCEC excluded; Cocoa and Texas
  City planned). A confirm/match action links a candidate to a facility and copies no fields.
- `fixtures/synthetic/roster_20.csv` (A§11; 20 facilities, more than 20 accounts).
- Roster import CSV/XLSX: preview (row errors, duplicates, unknown columns, candidate match proposals) → confirm.
- Scope classification. Events `account_fact.changed`. Readiness checks `roster_fields_confirmed` and
  `scope_classified`.
- **Tests:**
  - Imports: 20-facility import via CSV and via XLSX; multi-account sites.
  - Research candidates:
    - 13 candidates with blank utility/rate. An Orlando roster row proposes the match and creates no duplicate.
    - Confirming copies no research fields. Candidates are excluded from confirmed lists.
  - Edits keep provenance. Archived ≠ deleted.

#### S13. Documents, pages, source refs, synthetic PDF generator — R02, R03, §9.3
- Upload (PDF/CSV/XLSX; type/size validation), hash dedupe (returns original), authority, URL snapshot rule.
- Page/line extraction; spreadsheet normalization; unreadable pages → document request placeholder (wired in
  S20b) and manual-transcription source_refs.
- Source_ref validation (PDF quote / cell value); `replace` (successor + event); extraction retry without
  re-upload.
- `tariff_app.synthetic.pdfgen` (A§7).
- **Tests:**
  - Generated PDFs: identical specs → identical bytes; a blank page is unreadable.
  - Uploads: duplicate upload; URL without file → 422; the same URL with new bytes → two documents.
  - Source refs: quote not on page → 422; sheet locator → cell text.
  - Extractor raises → document kept, retry succeeds. latin-1 enforcement.

#### S14a. Tariff schedules & versions — R03, R04, §9.1, §9.3, A§6.1, A§6.10
- Schedules; versions as draft CRUD with validate-on-save; copy-on-write from approved; approval rules (no
  errors, source snapshot, governing-source rule, no open conflicts).
- Derived intervals; correction → superseded.
- Component inventory with support states and passages.
- `source_conflict` CRUD/resolve. Fixture importer (A§11). Preview endpoint (non-persisted). Event
  `tariff_version.approved`.
- **Tests:**
  - Version lifecycle:
    - Approve, then edit → draft; approval blocked with errors / without source document / summary-only refs.
    - A conflict blocks; resolving with a summary ref → 422.
    - v1 [2024-08-30,∞) + v2 [2026-06-01,∞): Nov-2025 → v1, Jul-2026 → v2. A correction supersedes only the
      corrected version.
  - Import round-trip: every component's refs resolve to a stored quote on the stated page.
  - Preview creates no rows.

#### S14b. Packages, assignments, mapping rules, package resolution — A§6.2, R04
- Package + package versions (members with windows, precedence, override reasons; draft → approved).
- `resolve_package_def`. Assignments proposed → approved with an entity/territory check; the allocation rule is
  approved separately.
- line_mapping_rules (proposed → approved). Affected-analyses query (stub returning [] until S16b).
- Readiness checks `package_version_approved`, `assignment_approved`, `versions_cover_invoice_periods`,
  `components_supported`. Events.
- **Tests:**
  - Assignments: wrong territory rejected.
  - Package resolution:
    - A member added 2026-06 is ignored for 2026-03.
    - A rider with no approved version for the month → MissingSource; base still resolves.
    - A cross-version period returns both versions.
  - Package versions: editing an approved package creates a draft; an override without a reason → 422.

#### S15a. Heuristic invoice extractor — A§7 grammar, R02
- Pure function `list[PageText] → InvoiceProposal` with locators, catalog alias mapping and sign normalization.
- **Tests:**
  - Rendered synthetic invoice → every field, line and determinant with line locators.
  - Formats: ¢/kWh rate; `1,234.56 CR` and `(1,234.56)` → −1234.56.
  - Unknown label → warning; missing field → null (never 0).

#### S15b. Intake & confirm — A§6.5, R02, R01 (ambiguous assignment)
- Extraction proposals persisted; confirm with corrections (idempotent) → invoice + lines + input set v1
  (measured/billed/contract demand distinct, provenance, source refs).
- Required fields → unresolved_fields. Duplicate/revision states + reject-duplicate/supersede actions.
- Ambiguous account matching + confirm action. billing_month proposal. Events.
- **Tests:**
  - Extraction and confirm: a missing period_end is not defaulted and blocks; double confirm → one invoice.
  - Duplicates:
    - Same bytes → original returned. A re-rendered identical bill → probable_duplicate → reject.
    - A changed line → revision_candidate → supersede v2.
  - Ambiguous → confirm; demand types distinct.

#### S15c. Corrections, manual entry, mappings & calc context — A§6.3 build_calc_context, R02, R06 mapping
- Correction events on lines and inputs → new input-set versions; manual entry (provenance manual); account
  exceptions CRUD.
- Mapping proposals: approved rules first (reviewed=true, origin=rule), then ComponentDef.invoice_labels
  (reviewed=false, label_match); manual mapping and review actions; double-mapping validation.
- `build_calc_context` (facts overlay, conflicts, series from prior months). Readiness check
  `invoice_labels_mapped`. Events.
- **Tests:**
  - Corrections: correction history kept; new input-set version.
  - Calc context:
    - A conflicting fact vs input → blocking issue.
    - Series from a superseded invoice excluded; null history counts as missing.
  - Mappings: label-match mapping unreviewed until confirmed.

#### S15d. Billing-summary import — A§7, R02
- CSV/XLSX upload → column mapping → preview (row errors, conflicts) → confirm → input values / history with
  cell source_refs; re-import of the same file is a duplicate. Event `billing_history.imported`.
- **Tests:** 24 months of measured demand feed a lookback; a baseline row with an as-of month; an unknown key is a
  row error; a cell quote mismatch → 422.

#### S15e. Optional AI proposals (mocked) — A§3.8, A§7, R02, R03, R08
- `ClaudeInvoiceExtractor`, a tariff rule-draft endpoint, and an explanation-draft endpoint. All are disabled
  without a key, use structured output and source validation, and only create proposals.
- **Tests (mock client):** hallucinated value dropped; injected instructions in document text are ignored
  (output still validated); disabled → 409 `ai_unavailable`.

#### S16a. Analysis run — A§6.3, R04, R05, R06, R08
- Create: assignment check, `resolve_package_def` + `build_calc_context`, snapshot, engine → reconcile →
  explain, typed result, initial assessment, fingerprint idempotency (partial unique index, concurrent-safe).
- Rerun → new version; reproducibility endpoint; account exceptions surface as explanation candidates.
- **Tests:**
  - Identical inputs → identical result JSON; retry → same analysis; two threads → one analysis; dict-order
    shuffle → same fingerprint.
  - No approved assignment → 409; draft versions never executed.
  - Reconciliation coverage persisted (partial with an unmapped line).

#### S16b. Lifecycle & staleness — A§6.3, R04, §5 review states, §7
- State machine (partial ack, stale guard, reviewed immutability).
- Event subscribers recompute fingerprints → stale + reasons; `newer_inputs_or_rules_exist`; affected-analyses
  listing (fills the S14b stub).
- **Tests:**
  - Staleness: a parametrized test over each ingredient (contract fact, prior-month demand, package version,
    mapping rule, document replace, input correction, invoice supersede) → draft stale with the right reason.
  - Lifecycle: a reviewed analysis is unchanged and flagged; a stale draft cannot transition; rerun → new
    version.

#### S17. Assessments, explanation reviews, review decisions — A§6.4, R07, R08
- **Tests:**
  - Reevaluation:
    - Threshold change → old assessment untouched, new one differs.
    - Reevaluate after a rule change + engine bump uses stored amounts (calculate spy not called).
    - Reevaluate on a reviewed analysis → 201, status unchanged; idempotent per settings version.
  - Explanations: a user explanation without evidence → 422.
  - Review: stores reviewed_assessment_id + open findings snapshot; the reviewed label wording.

#### S18. Findings lifecycle & account exceptions — A§6.12, R09
- **Tests:**
  - Every transition guard.
  - Distinct records: potential discrepancy / confirmed error / credit; a credit without a document → 422.
  - A finding survives a rerun; an exception surfaces on a later review as awaiting_confirmation.

#### S19. Analysis exports — R10, A§8 exports
- CSV + printable HTML report per analysis + assessment.
- **Tests:**
  - CSV parsed with DictReader: every number equals the saved JSON (Decimal).
  - Every R10 field is present: IDs, versions, notice, demand status, coverage + uncovered, threshold of the
    *selected* assessment, tolerances or "Reference validation pending", assumptions, source refs, decision,
    findings (at review + current).
  - A 2% reevaluation keeps the v1 export at 1%. Older or superseded → banner. SYNTHETIC labels.

#### S20a. Reference cases & runs — A§6.7, §8 reference calculation, §9.2, D06, D08, demo step 5
- Case import from fixtures; approval; "save analysis as acceptance fixture" (JSON export/import).
- Runs (approved versions), reclassify on tolerance change, held-out rule, computed package reference status.
- **Tests:**
  - An unapproved passing case doesn't count; a synthetic case doesn't validate a real account.
  - Held-out earlier than setup → 422. Reclassify creates new runs without recalculation.
  - Unset tolerances → pending → package pending. Fixture JSON round-trip.

#### S20b. Document requests & account readiness/enablement — A§6.8, A§6.9, R01, R04, §10
- `refresh_document_requests`; request receive/waive; AccountReadiness service + `POST /accounts/{id}/enable`;
  lapse events.
- **Tests:**
  - Document requests:
    - 10 of 12 bills → exactly 2 D02 requests naming the months.
    - A LIPS package → baseline D05; a 24-month lookback → 12 extra months.
    - Upload marks received.
  - Readiness: a configured account with partial calc and an unconfigured account with a complete analysis show
    statuses independently.
  - Enablement:
    - Blocked by a failing run (`known_incorrect_calculation`), by an awaiting_implementation component and by
      open D01–D04 requests.
    - Enablement lapses when a blocker reappears.

#### S21. Comparison scenarios service & exports — A§6.11, R11, R12, R10
- Scenario CRUD with versions + diff; candidates with the eligibility model and evidence validation; inputs
  (horizon/history/parameter assumptions) incl. CSV import via the S15d parser.
- Run → immutable result; comparison CSV + printable report; planned facility without invoices; D07 requests.
- **Tests:**
  - Six-candidate run on the fixture.
  - Eligibility: `eligible` without evidence → 422; closed without existing evidence excluded.
  - Tolerances: unset → every candidate `reference_pending`; after tolerances + reference runs → eligible
    modeled candidates rank.
  - Versions: an edited scenario keeps v1 and the diff shows changed assumptions.
  - Exports: export numbers equal the run; partial TOU + scenario-estimate label in exports.

#### S22. Portfolio queue — PRD §7, R01
- `GET /portfolio/queue` filters: facility, utility, period, coverage, review state, threshold flag. Lists
  awaiting-setup accounts and a separate research-candidates section; configuration vs calculation coverage
  side by side.
- **Tests:** each filter; 20 facilities; candidates segregated; awaiting-setup visible; flags use the current
  assessment.

#### S23a. Demo manifest & workspace seed — A§11 demo_manifest, PRD §8, §10.1
- `demo_manifest.json`: SYN-F01..F20, each with ≥1 in-scope account; five pattern demo accounts; ≥2 awaiting-setup
  accounts; host-site account; planned SYN-F20.
- `python -m tariff_app.seed --reset [--profile minimal|full] [--demo-tolerances]` with `FixedClock`:
  settings v1 defaults (tolerances unset), research candidates, facilities/accounts/facts, documents + tariff
  imports + approvals, packages, assignments, mapping rules.
- **Tests:** a minimal profile seeds twice identically; counts; everything addressable by code.

#### S23b. Demo seed — invoices, analyses, findings, scenario, reference runs
- 12 months of generated invoices for the five demo accounts (2025-10..2026-09) and a 2026-09 invoice for every
  other in-scope account.
- Named cases (rate_error, revised_bill, duplicate_upload, negative_credit, missing_rider_factor,
  billed_demand_only, cross_version, offsetting_lines, threshold_exact_1pct, threshold_above_1pct).
- Analyses, a few findings, approved reference cases + runs (pending under v1), the Florida scenario on SYN-F20.
  Full seed ≤ 30 s, built once per pytest session as a template.
- **Tests:** every seeded analysis is reproducible; each named case shows its intended flag/coverage; tolerances
  unset → pending.

#### S24a. Acceptance matrix & traceability — PRD §8, A§10
- `tests/acceptance/`: one module per matrix row + additional reference cases, using the seed template or
  fixtures via the service/API layer.
- Static AST meta-test: every backend-layer ID covered, no unknown IDs. `make traceability` → `docs/TRACEABILITY.md`.
- **Tests:** the suite itself and the meta-test.

#### S24b. UI read models & OpenAPI contract — A§8 read models, A§9
- `GET /views/analyses/{id}/review`, `/views/accounts/{id}/overview`, `/views/comparison-scenarios/{id}`.
- `tariff_app.openapi_export` + `make openapi` / `openapi-check` (added to `make check`); a test fails on
  schemaless response objects.
- **Tests:** each view's fields against seeded named cases; OpenAPI determinism; no `additionalProperties: {}` /
  untyped objects in responses.

### P3 — Frontend (`frontend/`)
Every UI step: tests tagged `// req: ID`, mocked API via `mockApi`, generated types only. Prober checks the
running UI with Playwright screenshots on a seeded workspace.

#### S25. Frontend foundation — A§9
- Generated client + `ApiProvider`; app shell/nav; routing; Query client; error boundary mapping `ErrorOut`.
- `lib/decimal.ts`; shared components; meta/AI/SYNTHETIC banner; `mockApi`; `renderWithProviders`.
- **Tests:**
  - `formatDecimal` / `formatPercent` cases (A§9).
  - Components: Money tooltip + rounded label; Variance sign / flag / n/a; CoverageBadge states.
  - Routing smoke; error mapping.
  - A lint/test banning Number/parseFloat on amounts.

#### S26. Portfolio, facilities, accounts, research candidates, roster import UI — R01, §7, A§6.9
- **Tests:** queue filters; awaiting-setup and candidates sections; the account page shows configuration vs
  calculation coverage, readiness blockers, document requests and validated components; the import wizard shows
  error rows; candidate confirm.

#### S27a. Documents UI — R02
- Upload with duplicate prompts, viewer (pages/lines, unreadable pages), replace, extraction retry.
- **Tests:** duplicate prompt; unreadable page request; line highlight for a source_ref.

#### S27b. Invoice intake UI — R02, R06 mapping
- Proposal review with source highlights; confirm/correct (reason required; units/dates; demand types distinct);
  unresolved fields; duplicate/revision resolution.
- Ambiguous account; input-set history; mapping review; billing-summary import wizard.
- **Tests:** correction requires a reason; demand types distinct; unresolved fields block analysis; revision
  supersede flow.

#### S28a. Tariff library UI — R03, R04
- Schedules/versions; component inventory with three support badges; component detail (formula, params, units,
  applicability, rounding, deps, exact passage); draft JSON editor showing validation issues.
- Approve with reason (disabled with errors); conflicts; preview.
- **Tests:** approve disabled with errors; edit-approved creates a draft; passage shown; summary-only warning.

#### S28b. Packages, assignments, mapping rules, reference cases UI — R04, §9.2
- Package versions, assignment approval and allocation rule, mapping rules, affected analyses + rerun, reference
  cases/runs (pass/fail/pending), save-as-fixture.
- **Tests:** affected list + rerun; pending label when tolerances are unset; approval flows.

#### S29a. Review screen (read-only) — PRD §5 tables, R05–R08, REVIEW-SCREEN
- Built on `GET /views/analyses/{id}/review`.
- **Tests:** one per §5 question across complete / partial / billed-demand-only / expected-zero /
  unset-tolerance variants:
  - Header fields.
  - Coverage badge + exact notice + "Demand determination: unchecked" + input requests.
  - Subtotals: covered subtotals and no full total when partial; a separate bridge.
  - Row values: signed diff / pct / flag; "n/a" pct; offsetting rows visible.
  - Evidence drawer with exact passage, trace, invoice line source and input provenance.
  - Rounded-display label.

#### S29b. Review actions — R04, R07–R10, review states
- Source-linked "Correct value" (reason → new version → stale banner → rerun); explanation confirm/reject/add;
  partial acknowledgment; submit/review decision with the fixed label.
- Create a finding from a row; reevaluate (new assessment visible); version history; CSV/report links.
- **Tests:** ack required; stale blocks submit; reevaluate shows v2 and keeps v1; finding creation; export links
  carry the assessment.

#### S30. Findings workspace — R09
- **Tests:** list filters; transitions and guards (outcome on close, below-threshold check); confirmed error;
  credit receipt requires a document.

#### S31. Comparison UI — R11, R12, §6
- Scenario list/editor: horizon, shared/history inputs, approvals for reuse/forecast.
- Candidate editor: the eligibility model with evidence, conditions, conflicts, commitments, unmodeled
  conditions.
- Run results: components, totals, baseline diff, coverage, missing TOU requests, ranking, exclusion reasons,
  cheapest label, scenario-estimate banner. Version diff; exports.
- **Tests:** excluded candidates are not ranked and show reasons; the cheapest label is null with k=0; diff view.

#### S32. Settings UI — R07, §9.2, D08
- **Tests:** threshold/tolerance edit with reason and meaning text; overrides; history; "reevaluate affected
  analyses" guidance.

### P4 — Integration

#### S33. Single-process serving & E2E demo — PRD §10.1, completion checks
- SPA fallback route (TestClient tested); `make e2e` on a seeded temp workspace.
- One serial demo spec (DEMO-1..5 in PRD order):
  1. Facility/account with entity, tariff and contract terms.
  2. Review an invoice: quantities and clauses, rider/history handling.
  3. Explain a difference → finding → export.
  4. Compare six candidates: all excluded "Reference validation pending".
  5. Rules + passages + reference results → set tolerances → reference runs pass → rerun the comparison → ranking
     + cheapest label → save fixture.
- Further scenarios:
  - Correct a determinant → stale → rerun v2 (v1 unchanged).
  - Exactly 1% (unflagged) vs 1.001% (flagged).
  - Threshold edit → reevaluate keeps the prior assessment.
  - The account page shows validated components, document requests and awaiting_implementation components.
  - The 20-facility queue.
- Extend the traceability scan to frontend/E2E layers.
- **Tests:** E2E suite green, with screenshots kept as artifacts.

#### S34. Final audit & documentation
- Adversarial audit, requirement by requirement, against the running app; fix gaps.
- Docs: README walkthrough, user guide, known limitations; traceability regenerated.
- **Tests:** `make check` + `make e2e` green.
