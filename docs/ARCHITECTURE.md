# Utility Tariff Interpreter — Architecture Contract (MVP, v2)

This document is the **binding technical contract** for every implementation step in `docs/PLAN.md`.
Requirements come from `docs/PRD.md` (cited as `PRD §n` / `R01`–`R12`). If this document and the PRD
disagree, the PRD wins and this document must be corrected in the same change. v2 incorporates an
adversarial review (PRD coverage, tariff-domain expressiveness, execution feasibility).

---

## 0. Product summary

A single-user web application for an energy manager (Michael) covering ~20 facilities that:

1. Holds a facility/account workspace (R01) and ingests invoices, tariffs, rosters and billing summaries with
   source-linked extraction and explicit user confirmation (R02).
2. Stores tariffs as **structured, versioned, approved rule components** with exact source passages (R03, R04).
3. Executes approved rules **deterministically** on invoice-reported quantities (R05), reconciles them line by
   line against the bill (R06), flags differences against an editable 1% threshold (R07), explains differences
   from evidence (R08), tracks findings (R09) and exports evidence (R10).
4. Compares a bounded set of candidate tariffs on a common, explicit operating scenario (R11, R12).

Non-goals (PRD §1 "Deferred"): interval-data reconstruction, automatic tariff discovery, rate-case monitoring,
other commodities, procurement, enterprise integrations, multi-user administration, authentication.

---

## 1. Technology stack

| Layer | Choice | Notes |
|---|---|---|
| Language (backend) | Python 3.13 | `decimal.Decimal` for all money/quantities. |
| Env / deps | `uv` (lockfile `backend/uv.lock`) | `uv sync --locked`, `uv run ...` |
| Web API | FastAPI + Pydantic v2 | JSON API under `/api/v1`; `FastAPI(separate_input_output_schemas=False)`. |
| Persistence | SQLAlchemy 2.0 (typed ORM) + Alembic, SQLite (WAL, FK on) | Postgres-compatible schema. |
| PDF text | `pdfplumber` | Page + line text with positions. Text-readable PDFs only (R02). |
| Spreadsheets | `openpyxl` (XLSX), stdlib `csv` | |
| Reports | Jinja2 HTML with print CSS | Printable evidence reports (R10). |
| Synthetic PDFs | `fpdf2` (built-in Helvetica, latin-1 text, fixed creation date) | Deterministic bytes. |
| AI (optional) | `anthropic` SDK; model `TARIFF_AI_MODEL` (default `claude-opus-5-5`) | Disabled without `ANTHROPIC_API_KEY`; always mocked in tests. |
| Backend QA | pytest (+`pytest-xdist`), hypothesis, pytest-cov, ruff, mypy (strict for `tariff_engine`) | |
| Frontend | Vite 8 + React 19 + TypeScript 5.9 (strict, `noUncheckedIndexedAccess`) | TS pinned to 5.9 for `openapi-typescript`. |
| Frontend data | TanStack Query v5, `openapi-fetch` + `openapi-typescript` generated types | No hand-written API types. |
| Routing | `react-router` 7.x (data router `createBrowserRouter`) | |
| Styling | Plain CSS with design tokens (CSS variables) | No CSS framework. |
| Frontend QA | Vitest 5 + Testing Library + jsdom, oxlint (`--deny-warnings`), `tsc -b --noEmit` | |
| E2E | `@playwright/test` 1.64 with `executablePath=/opt/pw-browsers/chromium` when present | Never `playwright install` in cloud sessions. |

---

## 2. Repository layout

```
/CLAUDE.md  /Makefile  /README.md
/docs/PRD.md ARCHITECTURE.md PLAN.md requirements.yaml TRACEABILITY.md(generated)
/backend/
  pyproject.toml uv.lock alembic.ini
  migrations/                     Alembic (one revision per step that changes schema: sNN_<area>)
  src/tariff_engine/              PURE deterministic engine (no I/O, DB, web, AI, clock, randomness)
  src/tariff_app/                 FastAPI app, persistence, services, extraction, exports, seed
    api/ db/ models/ schemas/ services/ extraction/ synthetic/ reports/ seed/
  tests/engine/ tests/app/ tests/acceptance/
  tests/_probes/                  (gitignored; excluded from make check) adversarial scratch tests
  fixtures/synthetic/             SYNTHETIC tariffs, packages, reference cases, roster, scenario, demo manifest
/frontend/
  src/api/ (openapi.json, schema.d.ts, client.ts)  src/app/  src/features/<area>/  src/components/
  src/lib/ (decimal.ts …)  src/test/ (setup.ts, mockApi.ts, render.tsx)  e2e/
```

`tariff_engine` must never import `tariff_app`, SQLAlchemy, FastAPI, pdfplumber, anthropic, `datetime.now`,
`date.today`, `random`, `uuid`, `time`, or use `float`. An engine test (`tests/engine/test_purity.py`) enforces
this by AST inspection of every module in the package. Imports are an **allowlist** of top-level modules:
stdlib `__future__, abc, ast, bisect, calendar, collections, contextlib, dataclasses, datetime (date/timedelta),
decimal, enum, functools, graphlib, hashlib, heapq, itertools, json, operator, re, types, typing`, plus
`pydantic`, `pydantic_core` (pydantic's exactly-pinned core, for `PydanticCustomError`) and the package itself
(absolute or relative). Everything else fails — app/DB/web/document/AI libraries, clock/randomness (`time,
random, secrets, uuid, zoneinfo`), I/O/process/environment (`os, sys, io, pathlib, codecs, pickle, socket,
http, urllib, subprocess, threading, asyncio, sqlite3, logging, importlib`, C backends such as `posix`/`_io`, …)
and float math (`math, cmath, statistics`). Widening the allowlist is a contract change to this paragraph. The
scan also rejects float/complex literals; the names `float, complex, open, print, input, eval, exec, compile,
__import__, breakpoint` and the dynamic-access names `getattr, setattr, delattr, globals, locals, vars,
__builtins__`; the attributes `.now/.utcnow/.today/.fromtimestamp/.utcfromtimestamp/.timestamp/.from_float/
.create_decimal_from_float/.__float__/.__complex__/.literal_eval/.__globals__/.__builtins__` (also when imported
by name, e.g. `from ast import literal_eval`); and `json.load`/`json.loads` without `parse_float=Decimal` (JSON
numbers would become floats; `from json import load(s)` is rejected so the check applies). The scan is static and
best-effort: int true division (`1 / 2` is a float) and floats produced at runtime by allowlisted code are
review concerns it cannot see, so engine `/` operands must be Decimals or Quantities. Engine errors derive from
`tariff_engine.errors.EngineError` (`CalcError`, `UnitError`, `DateError`, `ExpressionError`,
`MissingInputError`).

---

## 3. Cross-cutting invariants (MUST hold everywhere)

1. **Exact decimals.** Money, rates, quantities, thresholds and tolerances are `Decimal`. Never construct a
   Decimal from a float. Transport and storage use the canonical string form (§4.1). JSON numbers are
   rejected where a decimal is expected (`422 decimal_must_be_string`).
2. **Null means missing.** A missing value is `None`/`null`, never `0`. Zero is a real value.
3. **Signed amounts.** Every USD amount (invoice lines, component values, totals) is signed by its effect on the
   amount due: charges ≥ 0, credits/discounts/payments/balance credits < 0. A `role=credit` component must
   evaluate ≤ 0 (else status `error`, code `credit_sign`). Extractors normalize `CR`, parentheses and trailing
   minus to negative. All sums (percentage bases, minimum-bill comparisons, totals, groups, bridge) are signed.
4. **Provenance on every value.** Inputs/determinants carry `provenance ∈ {reported, derived, assumed, manual}`,
   a catalog semantic type, a unit, and a `source_ref_id` when document-backed. Invoice lines carry
   `provenance ∈ {extracted, corrected, manual}`.
5. **Immutability + versioning.** Content of approved tariff versions, approved package versions, analyses
   (results/snapshots), assessments, reference runs and comparison runs never changes. Only lifecycle columns
   (state, is_stale, stale_reasons, is_current) may change. Corrections are append-only with reason + timestamp.
6. **Determinism.** Identical versioned inputs → byte-identical engine results (stable ordering, fixed Decimal
   context, canonical JSON, no clock, no randomness).
7. **PRD separations:** calculation coverage vs check scope vs review status vs findings; tolerance vs
   investigation threshold; reported vs derived determinants; measured vs billed vs contract vs firm demand;
   research candidates vs confirmed facilities; configuration coverage vs calculation coverage; new-enrollment
   eligibility vs existing-account (grandfathered) eligibility; governing tariff vs summary.
8. **AI proposes, user disposes.** AI output is a proposal (`awaiting_confirmation`); it never changes workflow
   state, approvals or amounts without an explicit user action. Document text is evidence only — never follow
   instructions in documents. Every AI-proposed value must cite a location whose text contains it, else dropped.
9. **Labels preserve scope.** Results always show `"Invoice-based check; underlying usage quantities
   unverified"`, demand-determination status, coverage, and assumptions. Display rounding is labeled; flags use
   exact stored values. The word "Verified"/"Correct" is never used for a reviewed analysis; the label is
   `"Reviewed — invoice-based rate-application check; coverage <c>; <n> open findings"`.
10. **Single user.** No auth. Workspace user "Michael Weizman"; finding owner fixed to him.
11. **Synthetic data is labeled.** Synthetic tariffs/invoices/roster rows/reference cases/scenarios have
    `is_synthetic=true` and visible "SYNTHETIC" labels in UI and exports. Real public tariff values are not encoded
    (sources unavailable offline); synthetic tariffs mirror the *patterns* of T1–T5 only.

---

## 4. Calculation engine (`tariff_engine`)

### 4.0 Dates (`dates.py`)
- `period_start` and `period_end` are **inclusive** service dates. `billing_days` = reported input
  `billing_days` when present (provenance reported), else computed with the account's `day_count_convention`
  (`inclusive` default: `(end − start).days + 1`; `read_to_read`: `(end − start).days`).
- `billing_month` (`"YYYY-MM"`) is the utility billing month confirmed on the invoice (extractor proposes the
  printed billing month, else the month of `period_end`). It drives seasons, `by_period` factors, lookback windows.
- Version effective intervals are **half-open** `[effective_from, effective_to)`.
- Month arithmetic helpers: `month_add("2026-07", -11) == "2025-08"`, `months_between`, `month_days`.
- Comparison month `M`: `period_start` = first day, `period_end` = last day, `billing_days` = calendar days.
- API (S01): `DayCountConvention{inclusive, read_to_read}`; `billing_days(start, end, convention)`;
  `resolve_billing_days(start, end, convention, reported)` (reported wins; must be a whole number ≥ 1; `None` =
  not reported; an unknown convention raises `DateError` whether or not a count is reported). A period ending
  before it starts, or yielding 0 billing days (`read_to_read` with start = end), raises `DateError` — billing
  days is a proration denominator. Months are strict `"YYYY-MM"` (ASCII digits, years 0001–9999): the
  JSON-schema `MONTH_PATTERN` (no look-arounds) accepts exactly what the parser accepts, and the parser validates
  with it. Helpers: `parse_month`, `format_month`, `is_month`, `month_add`,
  `months_between(a, b) = b − a` (signed, so `month_add(a, months_between(a, b)) == b`), `month_range(first,
  last)` (inclusive, ascending, empty if first > last), `month_days`, `month_of(date)`, `month_bounds(M)`.
  Half-open helpers: `in_effective_interval(day, from, to|None)` and `interval_intersects_period(from, to|None,
  period_start, period_end)` (true iff ≥1 inclusive service day lies in `[from, to)`); an empty/reversed
  interval (`to ≤ from`) raises `DateError`. Every day argument must be a `datetime.date` that is **not** a
  `datetime` (else `TypeError`): a datetime would count 24-hour spans instead of calendar service dates. A
  reported `billing_days` must lie in `1..MAX_BILLING_DAYS` (3,652,059 = the days in years 0001–9999), checked
  before any integer conversion, else `DateError`.

### 4.1 Decimals, canonical forms (`decimals.py`)
- **Engine range** (S01): every engine decimal is a finite value the engine context can hold exactly —
  `adjusted() ≤ ENGINE_EMAX = 6144` (at most 6145 integer digits) and exponent `≥ ENGINE_ETINY = −6176` (at most
  6176 decimal places), the IEEE 754 decimal128 range. `in_engine_range(d)` tests it. Arithmetic results are in
  range by construction (overflow/underflow trap); `D()`, `DecimalStr` and `canonical_str` refuse anything
  outside it, so a short text such as `"1E+99999999999"` can never become a gigabyte-long plain string.
- `D(x: str | int | Decimal) -> Decimal`; raises `TypeError` for `float`/`bool` (and any other type),
  `ValueError` for text outside the ASCII grammar `[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?` (no
  whitespace, underscores, non-ASCII digits or NaN/Infinity spellings), non-finite Decimals and values outside
  the engine range. A coefficient longer than 34 digits is kept exactly (only arithmetic rounds to 34 digits).
- `engine_context()`: `localcontext` with `prec=34`, `rounding=ROUND_HALF_EVEN`, `Emax=6144`, `Emin=−6143`
  (decimal128), `clamp=0`, traps `InvalidOperation`, `DivisionByZero`, `Overflow`, **`Underflow`** (a tiny
  result is never silently rounded toward zero) **and `FloatOperation`** (any implicit float/Decimal mixing
  raises); every field explicit, fresh context per call. All engine arithmetic runs inside it.
- `RoundingMode` enum `HALF_UP, HALF_EVEN, DOWN, UP, CEILING, FLOOR`; `quantize(value, places, mode)`.
  Rounding quantizes the **canonical-unit magnitude** (USD, kW, kW·h, h, dimensionless): `places=5` on a
  `USD/kWh` rate means 0.001 cents. `places` may be negative (tens, hundreds); a zero result is never `-0`
  (`-0.004` → `0.00`); a non-finite value, a result beyond 34 digits or `places` outside `−6144..6176` (checked
  before any conversion) raises `CalcError`.
- `canonical_str(d)`: `format(d, "f")`, never exponent notation, `-0` rendered as `0` at the same scale; a value
  outside the engine range raises `ValueError`.
- `DecimalStr = Annotated[Decimal, BeforeValidator(accept a plain decimal str or a finite Decimal instance;
  reject every JSON/Python number — int included — bool and null → "decimal_must_be_string"),
  PlainSerializer(canonical_str, return_type=str, when_used="json"), WithJsonSchema({"type":"string",
  "pattern":"^-?\\d+(\\.\\d+)?$"})]`. Every engine model and API model uses `DecimalStr`. Strings must fully
  match `-?[0-9]+(\.[0-9]+)?` (ASCII digits; no exponent, `+`, whitespace or thousands separators) else
  `decimal_invalid_format`; a NaN/Infinity Decimal → `decimal_not_finite`; a string or Decimal outside the engine
  range → `decimal_out_of_range`. *(S01: ints are rejected too, because FastAPI validates `json.loads` output in
  python mode, so accepting `int` would let JSON integers through and break §3.1.)* Optional fields are
  `DecimalStr | None`; `null` stays `None`, never `0`.
- `canonical_json(obj) = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` over
  `model_dump(mode="json")` output; the **only** input to fingerprints/content hashes (sha256). It accepts a
  Pydantic model (dumped in JSON mode) or JSON-native data (`None`/bool/int/str/list/tuple/dict with str keys)
  and raises `TypeError` for anything else, including floats, raw Decimals and dates; text (values or keys)
  that is not valid Unicode (a lone surrogate, e.g. from JSON `"\ud800"`) raises `ValueError`.
  `canonical_sha256(obj)` = hex sha256 of its UTF-8 bytes.

### 4.2 Units (`units.py`)
Dimensional algebra over base symbols `USD, kW, kVAR, kVA, h, day, kV`. A `Unit` is an immutable canonical
mapping symbol→exponent; dimensionless is `Unit({})`.
- Aliases (scale to canonical): `USD`,`$` (1); `cents`,`¢` (0.01 USD); `mills` (0.001 USD); `kW`; `MW` (1000 kW);
  `kVAR`,`kvar`,`kVAr` (kVAR); `kVA`; `kWh` (kW·h); `MWh` (1000 kW·h); `kVARh`,`kvarh` (kVAR·h); `kVAh` (kVA·h);
  `h`,`hour`,`hours`; `day`,`days`; `kV`; `%`,`percent` (0.01, dimensionless); `1`/`""` (dimensionless).
  Compound with `/` and `*` (`USD/kWh`, `cents/kWh`, `USD/kW`, `USD/kVAR`, `USD/day`, `kWh/kW`).
- `parse_unit(text) -> (Unit, scale)`; `Quantity(value, unit, label)` normalizes to canonical scale on
  construction from a unit string (`6.912 cents/kWh` → `0.06912 USD/kWh`) and keeps `label` for display/trace.
  Every scale is an exact power of ten, so normalization is **exact** — all digits of the entered value are
  kept whatever the unit (a 37-digit value in `cents` is not rounded to 34) — and a normalized value outside the
  engine range (§4.1) is a `CalcError` (never a silent zero). `label_value` (value ÷ label scale) is exact too.
- `+ − min max` and comparisons need identical units → `UnitError` naming both. **Exception:** the literal `0`
  is unit-polymorphic in `+ − min max` and comparisons. `* /` combine units; divide by zero → `CalcError`.
- Reactive/apparent power are distinct dimensions. Explicit conversions only via functions (§4.3):
  `power_factor(p, q)`, `apparent(p, q)`, `as_reactive_equiv(x_kW)`.
- A charge/credit component's unit must be `USD`; a determinant's must equal its `output_unit`.
- Unit-text grammar (S01): aliases are **case-sensitive** (`kw`, `KWH` are unknown → `UnitError`); whitespace is
  ignored; `*` (or `·`) and `/` are left-associative (`USD/kW/h` = `USD/(kW·h)`); parentheses group; `^n` takes a
  non-zero integer exponent with |n| ≤ 9 (`kW^2`, `h^-1`); the numeral `1` is dimensionless (`1/h`); text is at
  most 100 characters. Scales combine exactly (`USD/MWh` → 0.001, `cents/%` → 1); an inexact, overflowing or
  underflowing scale is a `UnitError`, so a scale is never rounded or zero.
- Exponent bound (`MAX_UNIT_EXPONENT = 9`), one rule everywhere: every `Unit` keeps each base exponent in
  `−9..9` — constructing one outside it, directly or via `*`, `/`, `power` (so also `Quantity * /`), raises
  `UnitError` (e.g. a tenth `kW` factor on `kW^9`). In unit text the *effective* exponent of each name (the
  product of the `^n` applied to it through parentheses) is bounded too, so nesting cannot multiply the limit:
  `(kW^3)^3` = `kW^9`; `(kW^3)^4`, `kW^9*kW^9`, `((MW/kW)^9)^9` are `UnitError`. Hence `str(Unit)` — a
  deterministic display in base order `USD, kW, kVAR, kVA, kV, h, day` — always parses back with scale 1
  (`USD/kWh`, `kWh`, `kVARh`, `USD/(kW*kVAR)`, `kW^2`, `1/h`, `1` for dimensionless), and displaying a
  `Quantity` never fails: every value is in the engine range, and if the label re-expression would leave it (a
  value at the range edge shown in a smaller unit) the canonical value and `str(unit)` are shown instead.
- `Quantity` (immutable): `value` (canonical scale), `unit`, `label` (the unit text it was entered in; must parse to
  the same `Unit`; results of `+ − * /` use `str(unit)`, while unary `−`, `abs`, `quantize` and `qmin/qmax` keep
  the label of their (winning) operand, so `−(6.912 cents/kWh)` displays in cents; a zero literal adopting a unit
  gets `str(unit)`), `label_value` (value re-expressed in `label` units, e.g.
  `6.912` for `cents/kWh`), `is_zero_literal`. The polymorphic zero is `Quantity.zero_literal()` (S02 builds it
  for every zero-valued literal: `0`, `0.00`); it stays polymorphic only through unary `-`, `abs` and `+ − min max` with other zero
  literals — a computed zero (`x − x`, `0 * x`) has a definite unit, and rounding a zero literal
  (`Quantity.quantize`, S02 `round_to`) yields a plain dimensionless zero. Unit-checked operations: `+ −`,
  `qmin/qmax(...)` (ties keep the first operand), `< <= > >=` and `compare(a, b) → −1/0/1` (expression `==`/`!=`
  must use `compare`). Python `==`/`hash` are structural (value, unit, zero-literal flag; label ignored) and never
  raise. Mixing a `Quantity` with a raw number is a `TypeError`; arithmetic failures (÷0, overflow, underflow)
  → `CalcError`.

### 4.3 Expression language (`expressions.py`)
Safe formulas parsed with `ast.parse(mode="eval")` and a strict node whitelist. **Never `eval`.**
- Numbers: literal **source text** → `Decimal(text)` (never the float value of `ast.Constant`). Literals are
  dimensionless (except unit-polymorphic `0`); units via `q("200", "h")` or `q(200, "h")`. Use `D()` (§4.1) for
  the conversion: it bounds the magnitude to the engine range and refuses `_` digit separators, so the parser
  must strip or reject those itself.
- Names: built-ins (`billing_month` int, `billing_days` [day], `days_in_billing_month` [day], `period_year` int),
  then names resolved by the package resolver (§4.6). Dotted names are not allowed.
- Operators: `+ - * /`, unary `-`, comparisons (chains OK), `and or not`, `a if cond else b`.
  Disallowed: `**`, attributes, subscripts, lambdas, comprehensions, f-strings, starred/keyword args, other calls.
- Functions: `min(...)`, `max(...)`, `abs(x)`, `round_to(x, places[, "MODE"])` (default `"HALF_UP"`),
  `ceil(x)`, `floor(x)`, `sqrt(x)`, `q(number, "unit")`, `in_months(m1, ...)` (bool on billing month),
  `power_factor(p, q)` (p in kW or kW·h, q in kVAR or kVAR·h, same h exponent → dimensionless `p/sqrt(p²+q²)`),
  `apparent(p, q)` (→ kVA or kVA·h), `as_reactive_equiv(x)` (kW→kVAR, kW·h→kVAR·h; same magnitude; traced).
- API: `parse_expression(src) -> Expr` (raises `ExpressionError` with position), `referenced_names(expr)`
  (excludes functions/built-ins), `evaluate(expr, env) -> Quantity | bool`. Missing input → `MissingInputError
  (name)`. Limits: source ≤ 2,000 chars, AST depth ≤ 50.
- API detail (S02): `parse_expression` returns a frozen, text-cached `Expr{source, body: Node}`. Nodes are frozen
  dataclasses, each with a `Span{start, end, line, column}`: `Constant{value: Quantity, text}` (a signed number
  literal or a whole `q(...)`), `Name{id}`, `Unary{- | not}`, `Binary{+ - * /}`, `BoolOp{and | or, operands}`,
  `Compare{left, ops, comparators}`, `Conditional{condition, if_true, if_false}`, `Call{function, args}`,
  `RoundTo{operand, places, mode}`, `InMonths{months}`; `children`/`walk` traverse pre-order in source order
  (`x if c else y` visits `x`, `c`, `y`, although evaluation starts at `c`).
  `referenced_names` returns a sorted, distinct tuple. `evaluate_traced(expr, env) -> Evaluation{value,
  substitutions (name + value, first-use order), conversions (power_factor/apparent/as_reactive_equiv with
  arguments, result, span)}`; `evaluate` returns its value. `env: Mapping[str, Quantity | bool | int |
  MissingValue]` is built by the package resolver (S03/S04); an `int` is dimensionless (one outside the engine
  range, §4.1, is a `CalcError`); any other type is a `TypeError`. A result may be the polymorphic zero (formula
  `0`); callers place it in their unit. Parsing and evaluation never depend on the ambient decimal context: the
  cached tree holds exact literals and all arithmetic runs in `engine_context` (§3.6, §4.1).
- Built-ins (S02, `builtin_values(billing_month, billing_days)`): `billing_month` = month number 1–12 and
  `period_year` = year of the utility billing month (§4.0); `billing_days` = resolved billing days [day];
  `days_in_billing_month` = calendar days of the billing month [day]. Name resolution order (§4.6) puts them first.
- Literals and arguments (S02): every zero-valued literal (`0`, `0.00`) is the polymorphic zero; a `-` written
  directly before a number literal is part of it (`-1.5` is one `Constant`, negated exactly with `copy_negate`, so
  a 37-digit `-1234…567` keeps every digit; `-0` stays the polymorphic zero, never `−0`), while `-q(...)` stays a
  unary operation; hex/octal/binary/complex numbers, `True/False/None`, bytes, and text anywhere except `q`'s unit
  or `round_to`'s mode are rejected, and that text must be one plain quoted literal (no `u`/`r` prefix, triple
  quotes or implicit concatenation such as `"kW" "h"`, which would hide the effective unit or mode from the
  reviewer). Literal-only arguments are validated at parse time: `q`'s amount is a (signed) number literal or a
  quoted decimal (both exact) and its unit a text literal (`q(0, "kW")` is a zero **with** a unit, not
  polymorphic); `round_to`'s places a whole-number literal in `−6144..6176` and its mode a `RoundingMode` name;
  `in_months`
  takes distinct whole-number literals 1–12. `min`/`max` take ≥ 2 arguments. `ceil`/`floor` = `round_to(x, 0,
  CEILING/FLOOR)` on the canonical magnitude; `sqrt` halves even unit exponents (odd → `UnitError`, negative →
  `CalcError`, 34-digit correctly rounded); `power_factor`/`apparent` with `p = q = 0` → `CalcError` for the
  factor and `0` for the apparent power.
- Text (S02): names are ASCII `[A-Za-z][A-Za-z0-9_]*` and must appear verbatim (Python's NFKC identifier
  normalization would turn `ﬁx` into `fix`); `#` comments, backslashes, control characters other than tab and
  newline, U+2028/U+2029 and lone surrogates are rejected anywhere. A lexical pre-scan that mirrors Python's
  tokenizer runs before `ast.parse` and rejects a number glued to the next word (`1if x else 2`, which Python
  accepts with only a `SyntaxWarning`) and f-strings (whose fields are code), so the parser never warns: the engine
  prints nothing to stderr, and acceptance, message and position never depend on warning filters (`-W error`).
  Leading whitespace is skipped but positions refer to the original text. Depth counts nested expression nodes
  (a name or literal is 1; each operator, call or conditional adds 1, so a sum has at most 50 terms) and is checked
  iteratively before any recursion; Python's own parser limits also surface as `ExpressionError`.
- Kinds (S02): values are numbers (`Quantity`) or conditions (`bool`). Arithmetic, ordering and function
  arguments need numbers; `and/or/not` and `if` conditions need conditions; `==`/`!=` compare two numbers (via
  `Quantity.compare`) or two conditions. Evident mismatches fail at parse time, others when evaluated; both are
  `ExpressionError` with the node's position. Unit and arithmetic failures stay `UnitError`/`CalcError`.
- Missing values (S02, §3.2): a name absent from `env` or bound to `MissingValue` is *unknown*. Unknowns
  propagate through arithmetic and functions; a conditional with an unknown condition is unknown (its branches
  are not evaluated); `and`/`or` and comparison chains use three-valued logic, left to right with short-circuit
  (a definite `False` decides `and`, a definite `True` decides `or`, a definite `False` pair decides a chain).
  Errors follow the same left-to-right reading: an error (`CalcError`, `UnitError`, a kind `ExpressionError`) in an
  operand met before any unknown one propagates, but one met after an unknown operand (or unknown pair) makes the
  whole `and`/`or`/chain unknown with the names collected so far, and later operands are not consulted — that
  operand is reached only if the missing inputs turn out non-decisive (`has_meter and kwh / kw > q(200, "h")` with
  `has_meter` missing and `kw = 0` → `MissingInputError("has_meter")`, as for `… if has_meter else …`; a later
  definite `False` does not decide, since with `has_meter` true evaluation would fail first). An unknown result
  raises `MissingInputError(name, *more)`: `.name` is the first and `.names` every missing name the result depends
  on, in evaluation order (so S04 can request them all at once).
- Errors (S02): every `EngineError` has optional `offset` (0-based characters), `line` and `column` (1-based,
  characters), set by `locate(...)`; `str()` appends `(line L, column C)` when set. `ExpressionError{message,
  offset, line, column}` always has them; a `UnitError`/`CalcError` raised while evaluating gets the span of the
  innermost node whose own operation failed (`a + b / c` with `c = 0` → offset 4), keeping its class so S04's
  classification is unchanged. `ExpressionError` and `MissingInputError` live in `errors.py` and are re-exported by
  `expressions.py`.

### 4.4 Determinant catalog (`catalog.py`, pure data)
`CATALOG: dict[str, CatalogEntry{key, semantic_type, unit, series_capable, shared_compatible,
requires_definition_id, typical_source: invoice|account_fact|billing_summary|scenario, invoice_label_aliases}]`.

| key | semantic_type | unit | series | shared | def_id | source |
|---|---|---|---|---|---|---|
| `energy_kwh` | energy.total | kWh | ✓ | ✓ | – | invoice |
| `energy_on_peak_kwh` / `energy_off_peak_kwh` / `energy_shoulder_kwh` | energy.tou.on_peak / off_peak / shoulder | kWh | ✓ | ✓ (only if same definition_id) | required | invoice |
| `demand_measured_kw` | demand.measured | kW | ✓ | ✓ | optional (interval) | invoice |
| `demand_on_peak_measured_kw` | demand.measured.on_peak | kW | ✓ | ✓ (same def) | required | invoice |
| `demand_billed_kw` | demand.billed | kW | ✓ | ✗ | – | invoice |
| `demand_contract_kw` | demand.contract | kW | – | ✗ | – | account_fact |
| `demand_firm_kw` | demand.firm | kW | ✓ | ✗ | – | account_fact |
| `demand_historical_max_kw` | demand.historical_max | kW | – | ✗ | – | billing_summary (period = as-of month) |
| `reactive_demand_kvar` | reactive.demand | kVAR | ✓ | ✓ | – | invoice |
| `reactive_energy_kvarh` | reactive.energy | kVARh | ✓ | ✓ | – | invoice |
| `power_factor` | power_factor | 1 (fraction; `%` inputs normalized) | ✓ | ✓ | – | invoice |
| `service_voltage_kv` | service.voltage | kV | – | ✓ | – | account_fact |
| `billing_days` | period.days | day | – | – | – | invoice |

Rules: tariff-specific determinants use the prefix `x_<schedule_code_lower>_` and are never shared. Component
keys may not equal catalog keys (the derived billing demand component is `billing_demand_kw`). Extractors map
invoice labels to keys only through `invoice_label_aliases` (unknown labels → warnings, never new keys). A
meta-test asserts every determinant key used in `fixtures/synthetic/**` is in the catalog or `x_`-prefixed.

### 4.5 Rule model (`model.py`, Pydantic v2, frozen, decimals as `DecimalStr`)
```text
ParameterDef   { name, unit, value|None, by_period: {"YYYY-MM": dec}|None, scope: version|package (default version),
                 unresolved: bool=false, unresolved_note|None, description, source_refs: [str] }
               # exactly one of value/by_period unless unresolved=true (then both null)
DeterminantDef { key, semantic_type, unit, definition_id|None, description }   # validated against catalog
Rounding       { places: int, mode: RoundingMode }
Applicability  { months: [1..12]|None, condition: expr|None }
Fallback       { input: catalog key, scope_tag: str, label: str }   # role=determinant only; unit = output_unit
BaseSelector   = [component_key] | { roles: [charge|credit], exclude: [component_key] }
Calc (discriminated on `type`):
  fixed        { amount: expr }                                      → USD
  rate         { rate: expr, quantity: expr }                        → rate × quantity (unit-checked)
  tiered       { quantity: expr, tiers: [Tier], round_each_tier: Rounding|None }
     Tier      { size: expr|None, rate: expr|None, subtiers: [{ size: expr|None, rate: expr }]|None }
               # exactly one of rate/subtiers; size=None only on the last tier/subtier (open-ended);
               # sizes may reference determinants (q(200,"h") * billing_demand_kw → kWh); a band's quantity is
               # spread over its subtiers from zero; size<0 or quantity<0 → error negative_tier_quantity
  formula      { expr }
  percentage   { base: BaseSelector, rate: expr(dimensionless) }      → rate × Σ base (signed)
  minimum_bill { floor: expr(USD), against: BaseSelector }            → max(0, floor − Σ against)
  lookback     { series: catalog key | None, series_expr: expr | None,   # exactly one
                 window: { months_back: int≥1, include_current: bool } | { since_baseline: true },
                 months_filter: [1..12]|None, aggregate: max|min|mean|sum,
                 completeness: "all_months" | { min_points: int, approval_note: str },
                 baseline: { input: catalog key }|None }               # baseline InputValue.period = as-of month
ComponentDef   { key, name, role: charge|credit|determinant|subtotal, calc, output_unit, rounding|None,
                 applicability|None, fallback|None, check_scope_tag|None,
                 support_state: supported|awaiting_input|awaiting_implementation, support_note|None,
                 invoice_labels: [str], source_refs: [str], depends_on: [component_key],
                 publishes_series: catalog key|None, notes }
ExternalRef    { name, kind: component|parameter|determinant, unit }
TariffVersionDef { schedule_code, schedule_kind: base|rider|factor_table|service_rule|amendment,
                 utility_legal_entity, service_territory, version_label, version_ref|None (opaque app id),
                 effective_from, effective_to|None, effective_basis: service_dates|billing_month|statement_date,
                 parameters, determinants, components, external_refs }
PackageMember  { schedule_code, role, precedence: int (higher wins), member_from: date, member_to: date|None,
                 versions: [TariffVersionDef] (sorted, non-overlapping), missing: MissingSource|None,
                 overrides: [{ kind: component|parameter, key, target_schedule|None, reason }] }
MissingSource  { reason: "no_approved_version_for_period", provides: [name] }
PackageDef     { package_ref, members: [PackageMember] }
```
Lookback semantics: window months = `billing_month − months_back … billing_month − 1` (calendar), plus
`billing_month` if `include_current`; `since_baseline` = every month after the baseline's as-of month through
`billing_month − 1`. Required months = window months whose month number ∈ `months_filter` (all if null).
`all_months`: every required month must have a non-null series value, else `missing_input` with **one
InputRequest per missing month**. `min_points` relaxation adds check-scope note "ratchet history incomplete:
k of n months" and forces `demand_determination=unchecked`. The baseline value joins the aggregate.
`series_expr` is evaluated per required month using only that month's series determinants and parameters
(`by_period` resolved for that month); referencing components or nested lookbacks → validation error
`recursive_lookback`. A ratchet on prior **billing** demand uses the reported `demand_billed_kw` series; the
engine never recomputes prior months' billing demand recursively (comparison uses `publishes_series`, §4.10).

Validation (`validate_version`, `validate_package`) returns `ValidationIssue{severity: error|warning, code, path,
message}`. Codes include: `unknown_name` (undeclared external), `unresolved_external_ref`,
`external_ref_unit_mismatch`, `duplicate_key`, `override_without_reason`, `equal_precedence_collision`,
`open_tier_not_last`, `tier_rate_xor_subtiers`, `charge_not_usd`, `credit_role_requires_nonpositive` (static
when detectable), `cycle` (with path), `unit_mismatch`, `by_period_key_format`, `parameter_value_xor`,
`unknown_determinant`, `determinant_catalog_mismatch`, `definition_id_required`, `fallback_not_determinant`,
`fallback_unit_mismatch`, `recursive_lookback`, `component_key_is_catalog_key`, `overlapping_versions`,
`percent_out_of_range` (warning), `unresolved_parameter` (warning — version stays approvable; component reports
`missing_source`). Versions with `error` issues cannot be approved.

### 4.6 Package merge & name resolution (`package.py`)
- Component keys and determinant keys are **package-global**; parameters are **version-scoped** unless
  `scope=package` (e.g. factor tables export monthly factors).
- Name resolution inside an expression of version V: built-ins → V's parameters → package-scoped parameters →
  component keys → determinants/inputs. Names V uses but does not define must be declared in
  `V.external_refs` (kind + unit) and resolve to exactly one merged definition of that kind and unit.
- Collisions: component keys across members, package-scoped parameters across members, and a determinant
  declared with different semantic_type/unit/definition_id are errors unless an `override` (higher precedence,
  with reason) resolves them. An amendment overriding a base parameter names `target_schedule`. The trace records
  every applied override with its reason.
- `BaseSelector` with roles resolves at merge into an explicit sorted key list (recorded in trace and graph);
  `validate_package` warns on charge components neither selected nor excluded by any `{roles, exclude}` selector.
- Dependency graph = referenced names + selectors + `depends_on`; deterministic topological order (key-sorted
  tie-break); cycles → error with path.

### 4.7 Calculation (`calculator.py`)
```text
InputValue  { key, semantic_type, value: DecimalStr|None, unit, provenance, source_ref|None, period|None,
              definition_id|None, note|None }
CalcContext { period_start, period_end, billing_month, statement_date|None, billing_days|None (reported),
              day_count_convention, account_entity, account_territory,
              inputs: {key: InputValue}, series: {key: {"YYYY-MM": InputValue}} }
calculate(package, ctx, allocation: none|prorate_by_days = none) -> CalculationResult
```
1. **Source applicability.** Every member version's entity/territory must equal the account's → else coverage
   `blocked`, issue `wrong_utility_or_territory`. Members whose `[member_from, member_to)` does not intersect the
   period are dropped. Per member select versions: `billing_month` basis → the version containing the first day of
   `billing_month`; `statement_date` basis → the version containing the statement date; `service_dates` basis →
   all versions covering ≥1 service day. No version, or `missing` set → every component that (transitively)
   references a name in `missing.provides` is `missing_source` with `InputRequest{kind: source_version, key:
   schedule_code, period, document_hint: "D03"}`; others still calculate.
2. **Segments.** For `service_dates` members, split the period at every effective boundary; run the merged package
   once per segment. With `prorate_by_days`, each charge/credit value = `quantize(Σ value_seg × days_seg /
   billing_days, rounding)`; determinants/subtotals are not weighted (shown per segment in trace); tier sizes are
   not prorated. With `none`, every component of a straddling schedule and its dependents is `blocked` with
   `InputRequest{kind: allocation_rule}`.
3. **Merge & graph** per §4.6.
4. **Evaluate each component** in topological order:
   (a) `awaiting_implementation` → `unsupported`; `awaiting_input` → `missing_source` (request kind `document`);
   (b) applicability false → `not_applicable`, value `0` in its output unit;
   (c) primary status: `blocked` if a dependency is not calculated (not_applicable counts as calculated);
       `missing_source` if it uses an unresolved parameter (request kind `unresolved_term`) or a missing-source
       name; `missing_input` if a needed input is null/absent, a `by_period` factor lacks the billing month
       (request kind `factor`), or a lookback is incomplete (kind `history`, one per month);
   (d) **fallback**: if the primary status is `missing_input`, or `blocked` only by dependencies whose root causes
       are all `missing_input`, and the component has a `fallback` whose input is present with unit =
       output_unit → `calculated`, `fallback_used=true`, value = rounding(fallback input), trace records the
       bypassed reason; the bypassed InputRequests are still emitted with `blocking=false,
       satisfied_by_fallback=<key>`. Never for `error`, `unsupported`, `missing_source`, territory or allocation.
   (e) arithmetic/unit failure → `error` with message; credit sign violation → `error` `credit_sign`.
   (f) else `calculated`: `value = quantize(raw_value, rounding)` if rounding else `raw_value`. With
       `round_each_tier`, each tier subtotal is quantized and `raw_value = Σ` quantized tiers, then component
       rounding applies. **Every downstream reference uses `value`** (expressions, tier sizes, bases, minimum bill,
       reconciliation, flags, totals, reference classification); `raw_value` is for trace only.
   Each result records `trace` (expression text, substituted values with units, tier/subtier allocations, lookback
   months and values, overrides applied, conversions), `used_inputs` (with provenance and source refs),
   `used_parameters`, `source_refs`, `schedule_code`, `version_label`, `version_ref`, per-segment values.
5. **Coverage (calc):** applicable `charge|credit` components: `complete` = all calculated; `blocked` = structural
   block or none calculated; else `partial`. `expected_total` = Σ charge/credit values only if complete;
   `supported_subtotal` = Σ calculated charge/credit values; `uncovered` = `[{key, status, reason}]`.
6. **Check scope:** `{notice: "Invoice-based check; underlying usage quantities unverified",
   demand_determination: not_applicable | checked | unchecked, reasons: [...], notes: [...]}`;
   `not_applicable` if no applicable component has `check_scope_tag="demand_determination"`; `checked` iff every
   such component is calculated with `fallback_used=false` and no min_points relaxation; else `unchecked`.
7. **Result:**
```text
InputRequest { kind: determinant|history|factor|contract_term|source_version|allocation_rule|unresolved_term|document,
               key, semantic_type|None, period|None, description, needed_by: [key], blocking: bool,
               satisfied_by_fallback|None, document_hint: D01..D08|None }
ComponentResult { key, name, role, status: calculated|not_applicable|missing_input|missing_source|blocked|
               unsupported|error, value|None, raw_value|None, unit, fallback_used, bypassed_reason|None,
               blocking_keys, trace, used_inputs, used_parameters, source_refs, input_requests, message|None,
               schedule_code, version_label, version_ref|None, segment_values }
CalculationResult { engine_version, billing_month, period_start, period_end, billing_days, segments,
               resolved_versions: [{schedule_code, version_label, version_ref}], components, coverage,
               expected_total|None, supported_subtotal, uncovered, input_requests (deduped, sorted), check_scope,
               assumptions: [InputValue with provenance assumed|manual], blocking_issues, warnings }
```
All engine models evolve **additively only** so stored results stay readable.

### 4.8 Reconciliation, assessment & reference classification (`reconcile.py`)
```text
InvoiceLineIn { id, line_no, label, amount|None, category: current_charge|balance_item|informational,
                balance_type: prior_balance|payment|balance_credit|late_charge|prior_period_adjustment|other|None,
                quantity|None, quantity_unit|None, rate|None, rate_unit|None, source_ref|None, out_of_scope_reason|None }
MappingGroup  { line_ids: [str], component_keys: [str], reviewed: bool }
reconcile(calc, lines, groups, stated_amount_due|None) -> Reconciliation
assess(reconciliation, threshold) -> AssessmentResult
```
- **Group validity:** each line id and each charge/credit component key appears in at most one group
  (`ReconcileError double_mapped`). `group_key = "c:" + "+".join(sorted(component_keys))` when the group has
  components, else `"l:" + lowercase-whitespace-collapsed label of its lowest line_no` (stable across reruns and
  revised invoices). A group with ≥2 lines and ≥2 components is `coarse` (warning).
- **Group values:** `expected = Σ` contributions (calculated → value, not_applicable → exact `0`); any other
  component status → `expected = None` (row `expected_missing`, lists keys). `billed = Σ` line amounts; any null →
  `None` (row `billed_missing`); no lines → `0`.
- **Rows:** every group yields one row; additionally every applicable calculated charge/credit component with
  value ≠ 0 in no group yields a `not_billed` row (billed 0); uncovered components in no group yield
  `expected_missing` rows; calculated zero / not_applicable components in no group yield no row; in-scope
  current-charge lines in no group yield `unmapped_line` rows (expected None); lines with `out_of_scope_reason`
  yield `out_of_scope` rows (not compared). Row statuses: `compared, not_billed, expected_missing,
  billed_missing, unmapped_line, out_of_scope`. Rows carry `mapping_reviewed`, `coarse`, and `check_scope_notes`
  from their components (fallback used, assumed inputs, demand determination unchecked).
- **Difference & pct:** `difference = billed − expected` (signed) when both present; `pct = difference /
  |expected|` for display/export only, `None` when expected is 0 or either side is None.
- **Reconciliation coverage:** `blocked` if calc blocked; `complete` iff calc complete ∧ no row in
  {`expected_missing`, `billed_missing`, `unmapped_line`} ∧ every group reviewed ∧ every in-scope current-charge
  amount non-null; else `partial`. `uncovered` = calc uncovered ∪ such rows (with reason). **This is the
  coverage shown everywhere** (UI, queue, exports, readiness); calc coverage is stored separately.
- **Totals:** `comparable_billed_total` = Σ in-scope current-charge amounts (None if any null);
  `expected_total` = calc expected_total only when reconciliation coverage is complete; `total_difference`;
  `covered_expected_subtotal`, `covered_billed_subtotal`, `covered_difference` over rows with status ∈
  {`compared`, `not_billed`} (shown, never flagged); `supported_subtotal` (label "calculated components,
  including those not separately comparable"); `calculated_not_comparable` (calculated components inside
  `expected_missing` groups); `out_of_scope_total`.
- **Balance bridge:** `computed_amount_due = Σ all current_charge amounts (in-scope + out_of_scope) + Σ
  balance_item amounts` (informational excluded); None + status `incomplete` (listing null lines) if any amount is
  null; `bridge_difference = stated_amount_due − computed_amount_due`; items listed by balance_type
  (`prior_period_adjustment` is excluded from comparable charges and shown separately).
- **Flag rule (exact, no division):** `expected == 0 ∧ billed == 0` → matched; `expected == 0 ∧ billed ≠ 0` →
  flagged `zero_expected_nonzero_billed`; else flagged `exceeds_threshold` iff `|billed − expected| > threshold ×
  |expected|` (strict: exactly 1% is within). Applies to rows and to the total (only when expected_total exists).
  Rows with expected or billed None are not flagged and are marked `coverage_gap`.
- `AssessmentResult { threshold, rows: [{group_key, flagged, reason|None, coverage_gap}], total_flagged|None,
  flagged_count }`.
- **Reference classification (PRD §9.2):** `classify_reference(component_unit, calculated|None, reference,
  abs_tol|None, rel_tol|None) -> pass|fail|pending`. USD components: pass iff `|calc − ref| ≤ max(abs_tol,
  rel_tol × |ref|)` omitting unset terms; at `ref == 0` only the absolute term; `pending` when the applicable
  term(s) are unset. Non-USD components pass only on exact equality unless an override supplies an abs tolerance
  in that unit. Calculated None → `fail`. Overrides apply per field (a null field inherits the workspace value).
  Run overall: `failed` if any fail; else `pending` if any pending; else `passed`.

### 4.9 Explanations (`explain.py`)
Deterministic, evidence-backed generators over reconciliation rows. `Explanation{group_key, kind, text, status,
evidence: [EvidenceLink{type: rule|parameter|invoice_line|input|document, ref, label}]}`. No evidence → no
explanation (R08). Status by kind — `documented`: `rate_mismatch`, `quantity_mismatch` (both only for groups
with exactly one line and one `rate` component), `not_billed`, `unmapped_line`, `fallback_billed_demand`,
`ratchet_applied` (names the binding term), `minimum_bill_applied`, `missing_factor`; `awaiting_confirmation`:
`rounding_only` (|diff| ≤ $0.01 × number of rounded components), account exceptions (§6.12), AI or user drafts
until Michael confirms; `unresolved` ("No documented cause", evidence = the row's invoice line) when no generator
fires on a flagged/compared non-zero difference. Deterministic ordering.

### 4.10 Comparison (`comparison.py`)
```text
EligibilityFacts { confirmed_by_michael, schedule_status: open|closed_to_new_business|withdrawn,
  new_enrollment: {status: unconfirmed|eligible|ineligible, evidence_count},
  existing_account: {status: not_claimed|unconfirmed|documented|rejected, evidence_count,
                     permitted_use|None, scenario_within_permitted_use: bool},
  conditions: [{id, text, status: unknown|met|not_met, evidence_count}], evidence_conflict_open: bool,
  territory_exception_evidence_count: int }
derive_eligibility(facts, site_matches) -> (status: eligible|ineligible|unconfirmed, codes)
ScenarioIn  { name, version_no, months: ["YYYY-MM"], site_entity, site_territory,
              shared_inputs: {"YYYY-MM": {key: InputValue}}, history_inputs: {key: {"YYYY-MM": InputValue}},
              single_month_reuse: {approved: bool, source_month}|None, forecast: {approved: bool, note}|None }
CandidateIn { candidate_id, label, package: PackageDef (approved versions only), rules_approved: bool,
              eligibility: EligibilityFacts, reference_status: passed|failed|pending|none,
              specific_inputs: {"YYYY-MM": {key: InputValue}}, parameter_assumptions: {param: {"YYYY-MM": InputValue}},
              commitments: [str], unmodeled_conditions: [str] }
compare(scenario, candidates, baseline_id|None) -> ComparisonResult
```
- **Eligible** iff confirmed ∧ no open conflict ∧ every condition `met` with ≥1 evidence ∧ ((schedule open ∧
  new_enrollment eligible with ≥1 evidence) ∨ (closed_to_new_business ∧ existing_account documented with ≥1
  evidence ∧ permitted_use set ∧ scenario_within_permitted_use)). **Ineligible** if withdrawn, any condition
  `not_met`, or new_enrollment ineligible without documented existing eligibility. Else **unconfirmed**.
  Workspace facts (e.g. facility counts) never satisfy a condition.
- Site gate: candidate entity/territory must equal the site's unless `territory_exception_evidence_count ≥ 1`.
- Per candidate, per month (chronological): context with whole-month period; inputs = shared inputs that fill a
  candidate determinant only if the catalog says shared-compatible ∧ semantic_type ∧ unit ∧ definition_id
  match (null matches null), overlaid by candidate-specific inputs; series = `history_inputs` + earlier horizon
  months' inputs + values published by components with `publishes_series`; `parameter_assumptions` fill missing
  `by_period` months (provenance assumed). Months with no inputs use `single_month_reuse.source_month` only if
  approved (assumed, noted). A flat monthly total never supplies TOU quantities (R12).
- `modeled` = every month complete. **Rankable** iff modeled ∧ eligible ∧ rules_approved ∧ reference_status
  passed. Exclusion codes ⊆ {`not_confirmed`, `eligibility_unconfirmed`, `eligibility_evidence_missing`,
  `eligibility_conflict`, `ineligible`, `closed_without_existing_eligibility`, `outside_permitted_use`,
  `condition_unmet:<id>`, `condition_unknown:<id>`, `schedule_withdrawn`, `wrong_utility_or_territory`,
  `not_fully_modeled`, `rules_not_approved`, `reference_pending`, `reference_failed`, `no_reference_cases`}.
- Ranking by total ascending (ties by label). `baseline_diff = total − baseline_total` when both fully modeled,
  else None with reason `baseline_not_modeled`/`candidate_not_modeled`.
- `cheapest_label`: `"Lowest modeled cost for scenario '<name>' v<n> over <first>–<last> (<n> months) among <k>
  eligible, fully modeled candidates [<l1>, <l2>, …]: <label> (modeled estimate)"`; ties name all tied; None when
  k = 0. `is_scenario_estimate` = any assumed input/parameter, reuse applied, or forecast used.
```text
ComparisonResult { scenario_name, scenario_version, horizon: {first, last, months},
  candidates: [{ candidate_id, label, schedule_codes, version_labels, utility_entity, territory,
    entry_restrictions, eligibility_status, eligibility_codes, coverage, covered_months, missing_inputs,
    monthly: [{month, coverage, total|None}], component_totals: [{component_key, total}], total|None,
    baseline_diff|None, baseline_diff_reason|None, cost_drivers (top 3 |component_totals|, ties by key),
    commitments, unmodeled_conditions, unresolved, source_refs, assumptions, rankable, exclusions: [code] }],
  ranking: [candidate_id], cheapest_label|None, is_scenario_estimate }
```

### 4.11 Engine version
`tariff_engine.ENGINE_VERSION` (semver). Recorded on analyses, reference runs, comparison runs. Arithmetic
semantic changes bump it.

---

## 5. Persistence model (`tariff_app.models`)

IDs are opaque prefixed strings `"<prefix>_<16 hex>"`. Analyses also carry a human code `AN-000001`.
Timestamps are timezone-aware UTC via an injectable `Clock` (`SystemClock`, `FixedClock`). Decimal columns use
`DecimalText` (canonical string; **never filtered or sorted in SQL**). JSON columns use `canonical_json` and a
deserializer that rejects floats; read paths re-validate JSON into engine models. **Each step adds the tables it
needs in a new autogenerated Alembic revision named `sNN_<area>`; committed revisions are never edited.**
Alembic: `render_as_batch=True`, `compare_type=True`, `script_location=%(here)s/migrations`, template imports
`tariff_app.db.types`. A drift test asserts `upgrade head` == ORM metadata.

| Table | Key columns (beyond id/created_at) |
|---|---|
| `workspace` | name, user_display_name (`Michael Weizman`) |
| `settings_version` | version_no (unique), investigation_threshold (default `0.01`), abs_tolerance NULL, rel_tolerance NULL, reason |
| `tolerance_override` | settings_version_id, component_key, unit (default USD), abs_tolerance NULL, rel_tolerance NULL |
| `research_candidate` | location, state, evidence_text, source_refs (json S-ids), research_date, implied_status (`operating/planned/historical/host_site`), product_implication, status (`unconfirmed/matched/excluded`), matched_facility_id NULL — **no utility/tariff columns** |
| `facility` | code (unique), name, service_address, state, operating_status (`operating/planned/unknown`), archived_at NULL, is_synthetic, notes, updated_at |
| `production_unit` | facility_id, name, description |
| `utility_account` | facility_id, production_unit_id NULL, account_number, meter_ids (json), service_address NULL, utility_legal_entity NULL, service_territory NULL, tariff_code NULL, invoice_issuer NULL, billing_arrangement (`direct_utility/host_site/supplier_contract/unknown`), scope_classification (`regulated_in_scope/host_site/supplier_contract/pending`), scope_evidence_ref NULL, record_status (`confirmed/pending_confirmation`), enabled_at NULL, is_synthetic, notes — entity/territory/tariff_code edits record provenance via `correction_event` with source (`roster/invoice/contract/manual`) |
| `account_fact` | account_id, key (catalog key or account setting e.g. `day_count_convention`), semantic_type, value NULL, text_value NULL, unit, effective_from, effective_to NULL, provenance, source_ref_id NULL, note, superseded_by_id NULL |
| `account_exception` | account_id, description, component_key NULL, label_pattern NULL, evidence_refs (json), effective_from, effective_to NULL |
| `source_document` | kind (`invoice/tariff/tariff_summary/rider/factor_table/contract/roster/billing_summary/correspondence/credit_evidence/other`), authority (`governing/summary/correspondence/research/customer_record`), original_filename, content_hash (unique), storage_path, mime_type, size_bytes, source_url NULL, publication_date NULL, effective_date NULL, retrieved_at NULL, uploaded_at, facility_id NULL, account_id NULL, page_count, extraction_status (`pending/extracted/partial_unreadable/failed/not_applicable`), supersedes_id NULL, is_synthetic — CHECK source_url ⇒ retrieved_at + hash + storage |
| `document_page` | document_id, page_no, text, readable, lines (json [{line_no, text, x0, top}]); spreadsheets: one page per sheet, lines = rows, text = cells joined by ` \| ` |
| `source_ref` | document_id, page_no NULL, locator (`line 12` / `lines 12-14` / `Sheet1!B7`), quote NULL, transcription_note NULL — quote must occur (whitespace-normalized) in the page text (PDF) or equal the cell display value (sheet); quote NULL only for unreadable pages with transcription_note |
| `source_conflict` | subject_type (`parameter/component/applicability/eligibility`), subject_key, tariff_version_id NULL, candidate_id NULL, source_ref_ids (json, ≥2), status (`open/resolved`), governing_source_ref_id NULL, resolution_note, resolved_at NULL |
| `tariff_schedule` | code (unique), name, kind, utility_legal_entity, service_territory, status (`open/closed_to_new_business/withdrawn`), is_private, account_id NULL, is_synthetic, description |
| `tariff_version` | schedule_id, version_no, version_label, effective_from, effective_to NULL, effective_basis, source_document_id NULL, definition (json TariffVersionDef), content_hash, state (`draft/approved/superseded/rejected`), based_on_version_id NULL, edit_reason, approved_at NULL, approval_reason NULL |
| `tariff_package` | code (unique), name, utility_legal_entity, service_territory, description, is_synthetic |
| `tariff_package_version` | package_id, version_no, members (json [{schedule_id, role, precedence, member_from, member_to, overrides}]), state (`draft/approved/superseded`), based_on_id NULL, edit_reason, approved_at NULL, approval_reason NULL |
| `tariff_assignment` | account_id, package_id, effective_from, effective_to NULL, state (`proposed/approved/retired`), approved_at NULL, reason, allocation_rule (`none/prorate_by_days`), allocation_source_ref_id NULL, allocation_approved_at NULL |
| `line_mapping_rule` | account_id, label_pattern, match_type (`exact/contains/regex`), component_keys (json), category, balance_type NULL, out_of_scope_reason NULL, state (`proposed/approved`), reason |
| `extraction_proposal` | document_id, account_guess_id NULL, method (`heuristic/ai`), proposal (json), warnings (json), state (`pending/confirmed/discarded`), invoice_id NULL |
| `import_batch` | kind (`roster/billing_summary/scenario_inputs`), document_id, state (`previewed/confirmed/rejected`), preview (json), result (json) |
| `invoice` | account_id NULL, document_id NULL, proposal_id NULL, invoice_number NULL, statement_date NULL, period_start NULL, period_end NULL, billing_month NULL, amount_due NULL, version_no, supersedes_id NULL, duplicate_of_id NULL, state (`pending_confirmation/pending_duplicate_resolution/confirmed/superseded/rejected_duplicate`), duplicate_kind NULL (`probable_duplicate/revision_candidate`), assignment_status (`assigned/ambiguous/unassigned`), unresolved_fields (json), extraction_method (`heuristic/ai/manual`), is_synthetic |
| `invoice_line` | invoice_id, line_no, label, category, balance_type NULL, amount NULL, quantity NULL, quantity_unit, rate NULL, rate_unit, provenance, source_ref_id NULL, out_of_scope_reason NULL |
| `invoice_line_mapping` | invoice_id, group_no, invoice_line_id NULL, component_key NULL, origin (`rule/label_match/manual`), reviewed |
| `input_set` / `input_value` | account_id, invoice_id NULL, billing_month, period_start, period_end, version_no, supersedes_id NULL, reason / input_set_id, key, semantic_type, value NULL, unit, period NULL, definition_id NULL, provenance, source_ref_id NULL, note |
| `correction_event` | entity_type, entity_id, field, old_value, new_value, reason, source (`roster/invoice/contract/manual/correction`) |
| `analysis` | code, account_id, invoice_id, input_set_id, assignment_id, package_version_id, billing_month, version_no, supersedes_id NULL, is_current, status (`draft/ready_for_review/reviewed`), is_stale, stale_reasons (json), engine_version, tariff_version_ids (json), fingerprint, snapshot (json), result (json: calculation + reconciliation + explanations), coverage (reconciliation), calc_coverage, demand_determination, partial_acknowledged, partial_ack_note, reviewed_assessment_id NULL, open_findings_snapshot (json) NULL, review_conclusion NULL, review_notes NULL, reviewed_at NULL — partial unique index (fingerprint) WHERE is_current |
| `assessment` | analysis_id, settings_version_id, version_no, threshold, tolerance snapshot (json), result (json AssessmentResult), total_flagged NULL, flagged_count — unique (analysis_id, settings_version_id) |
| `explanation_review` | analysis_id, group_key, explanation_kind NULL, status, note, origin (`engine/ai/user`), text NULL, evidence (json) |
| `finding` / `finding_event` | account_id, analysis_id NULL, assessment_id NULL, invoice_id NULL, billing_month, group_key NULL, component_key NULL, kind (`difference/missing_item/coverage_gap`), amount NULL, state (`needs_review/explained/accepted_below_threshold/utility_inquiry/credit_requested/closed`), classification (`potential_discrepancy/confirmed_billing_error/not_an_error`), notes, next_action NULL, outcome NULL, explanation_ref NULL, evidence (json), owner (fixed) / finding_id, from_state, to_state, reason |
| `billing_error_confirmation` | finding_id, confirmed_on, utility_reference, amount, evidence_document_id NULL, note |
| `credit_receipt` | finding_id NOT NULL, document_id NOT NULL, amount, received_on, applied_invoice_id NULL, note |
| `reference_case` / `reference_run` | name, package_id, account_id NULL, origin (`synthetic_fixture/michael_worked_bill/saved_analysis`), is_synthetic, held_out, setup_case_ids (json), billing_month, context (json), allocation, expected (json {component_key: decimal}), expected_scope (json), derivation (json), from_analysis_id NULL, approved_at NULL, approval_note, version_no / case_id, settings_version_id, engine_version, package_version_id, calculated (json), results (json), overall (`passed/failed/pending`) |
| `document_request` | account_id NULL, facility_id NULL, scenario_id NULL, code (`D01`–`D08`), request_key (unique deterministic), description, periods (json), needed_by (json), generated_from, status (`open/received/waived`), fulfilled_by_document_id NULL, waived_reason NULL |
| `comparison_scenario` | facility_id, name, version_no, supersedes_id NULL, horizon_start, horizon_months, site_entity, site_territory, site_evidence_ref NULL, baseline_candidate_id NULL, single_month_reuse (json), forecast (json), change_note, is_synthetic |
| `comparison_candidate` | scenario_id, package_id, label, confirmed_by_michael, new_enrollment (json {status, evidence_refs}), existing_account (json {status, account_id, permitted_use, scenario_within_permitted_use, evidence_refs}), conditions (json [{id, text, source_ref_id, status, evidence_refs}]), evidence_conflict (json {open, note}), territory_exception_refs (json), commitments (json), unmodeled_conditions (json) |
| `scenario_input` | scenario_id, candidate_id NULL (shared), kind (`horizon/history/parameter_assumption`), billing_month, key, semantic_type, value NULL, unit, definition_id NULL, provenance, source_ref_id NULL, note |
| `comparison_run` | scenario_id, scenario_version_no, engine_version, settings_version_id, result (json ComparisonResult) |

---

## 6. Lifecycle, versioning & governance

### 6.1 Tariff versions
- `draft → approved` by Michael with reason + timestamp. Approval requires: zero validation errors; a
  `source_document_id` (stored snapshot; URL-only documents are impossible by schema); every `supported` component
  citing ≥1 source_ref in an `authority=governing` document (else `409 summary_not_calculation_basis` — the
  component may be saved as `awaiting_input` with request "governing tariff sheet for <component>"); no open
  `source_conflict` on it.
- Approved content (definition, effective_from, effective_to, content_hash) is immutable. **Derived interval** for
  selection: `[effective_from, min(effective_to, effective_from of the next approved, non-superseded version of the
  same schedule))`. Approving a newer version never edits older rows; two approved versions with the same
  effective_from are not allowed except via correction.
- Editing an approved version creates a new **draft** (`based_on_version_id`). If approved with the same
  effective_from, it is a **correction** and the prior version becomes `superseded` (still readable from snapshots,
  never selected for new runs).
- `POST /tariff-versions/{id}/preview` runs the engine on a supplied context without persisting (draft rules can
  be tried only this way).

### 6.2 Packages & assignments
- `tariff_package_version` members carry `member_from/member_to`, role, precedence, overrides (with reasons).
  Edits create a new draft; approval requires zero `validate_package` errors for every period in its members'
  ranges and a reason per override. Resolution uses the latest approved package version.
- `resolve_package_def(package_id, period_start, period_end, statement_date)` (S14b) is the **only** way to build
  engine input: loads every APPROVED, non-superseded version of each member schedule whose derived interval
  intersects the period (sorted), sets `missing` (with `provides` from the nearest approved version) when none,
  and returns `ResolvedPackage{package: PackageDef, package_version_id, tariff_version_ids}`.
- Assignment `proposed → approved` (Michael, reason) checks the package entity/territory equals the account's
  (`422 wrong_utility_or_territory`). The allocation rule (`none/prorate_by_days`) is approved separately with a
  source_ref to the governing rule.

### 6.3 Analyses
- Creation requires an **approved assignment covering the period** (`409 assignment_not_approved`), an invoice in
  `confirmed` state with no unresolved required fields (`409 invoice_not_ready`). Only approved versions execute;
  missing ones produce `missing_source` requests (partial), never a draft fallback.
- `build_calc_context(account, invoice, input_set)` (S15c): inputs = account facts effective on period_end
  (as InputValues) overlaid by input-set values; the same key with different values in both → blocking issue
  `conflicting_input`; series[key][YYYY-MM] = the latest confirmed, non-superseded input set's value for each prior
  billing month of the account, plus billing-summary history values; null counts as missing.
- **Snapshot & fingerprint:** `snapshot_core = {engine_version, package_version_id, members (schedule, version ids,
  content hashes, role, precedence, overrides), assignment_id, allocation_rule, invoice_id, invoice version_no,
  lines [{id, amount, category, balance_type, out_of_scope_reason}], mapping groups incl. reviewed, full
  CalcContext (all InputValues incl. series and facts with ids/source_ref ids)}`; `fingerprint =
  sha256(canonical_json(snapshot_core))`. Partial unique index on fingerprint where `is_current`; create is one
  transaction and, on conflict, returns the existing current analysis (safe retries, concurrent submits).
- **Rerun** creates a new version (`supersedes_id`, `is_current` moves), prior row untouched; the result records
  the approved versions used. Reproducibility check: re-run engine from the stored snapshot → identical result JSON.
- **States:** `draft → ready_for_review` (requires not stale; coverage ≠ complete requires
  `partial_acknowledged` + note) `→ reviewed` (requires conclusion; stores `reviewed_assessment_id` and
  `open_findings_snapshot`). Reviewed content is immutable (`409 analysis_reviewed` on mutation). Stale analyses
  cannot transition (`409 stale_analysis`).
- **Staleness:** services publish domain events on an in-process bus (`tariff_app.events`) from S12 on:
  `tariff_version.approved`, `package_version.approved`, `assignment.changed`, `invoice.superseded`,
  `invoice_line.corrected`, `input_set.versioned`, `mapping.changed`, `mapping_rule.changed`,
  `account_fact.changed`, `document.replaced`, `billing_history.imported`. The S16b subscriber recomputes the
  fingerprint of every current draft/ready analysis of affected accounts; if it differs → `is_stale=true`,
  `stale_reasons` = differing snapshot keys. Reviewed analyses expose computed `newer_inputs_or_rules_exist`.
  `GET /tariff-versions/{id}/affected-analyses` lists analyses to rerun explicitly.

### 6.4 Assessments & review
- An assessment is created with each analysis using current settings. A settings change never mutates it.
  `POST /analyses/{id}/reevaluate` (any status, incl. reviewed/stale) creates assessment vN+1 from stored amounts
  only (never calls `calculate`), idempotent per (analysis, settings_version). Reports/exports reference a
  specific assessment (default: `reviewed_assessment_id` if reviewed, else latest); later assessments are labeled
  "post-review".
- Explanation reviews: Michael confirms/rejects engine/AI drafts or adds his own explanation (must cite ≥1
  resolvable evidence link). Statuses `documented / awaiting_confirmation / unresolved`.

### 6.5 Invoices: duplicates, revisions, required fields
- Identical content hash → upload returns the original document (`200`, `duplicate_of`); no new invoice.
- Same account + invoice_number + billing period with every amount equal → `pending_duplicate_resolution /
  probable_duplicate`; any amount different → `revision_candidate`. Both block analysis creation
  (`409 duplicate_resolution_required`) until Michael chooses `POST /invoices/{id}/reject-duplicate` or
  `POST /invoices/{id}/supersede` (reason; prior → `superseded`, version_no+1, drafts stale, reviewed analyses
  labeled "invoice superseded").
- Required fields: account assignment, invoice_number, period_start, period_end, billing_month, amount_due, each
  line's amount and category. Confirming with any null keeps `pending_confirmation` with `unresolved_fields` as
  input requests; values are never defaulted. Ambiguous account → `assignment_status=ambiguous` + confirm action.
- `POST /extraction-proposals/{id}/confirm` is idempotent (second call returns the created invoice).

### 6.6 Settings
Append-only versions; current = highest version_no; defaults threshold `0.01`, tolerances unset. Overrides are
per component with a unit; null fields inherit the workspace value.

### 6.7 Reference cases & status
- `reference_case.origin ∈ {synthetic_fixture, michael_worked_bill, saved_analysis}`; only **approved** cases
  count. For a non-synthetic account only non-synthetic approved cases validate it; synthetic cases demonstrate
  software behavior. `held_out=true` requires a billing_month later than every setup case's (`422`).
- Runs use approved versions resolved for the case month; `POST /reference-cases/reclassify` creates new runs under
  the current settings reusing stored calculated amounts (no recalculation). Runs are immutable.
- **Package reference status (computed, never stored):** over approved cases of the package, using each case's
  latest run under the current settings version: `failed` if any failed; else `pending` if any pending; else
  `passed` if ≥1; `none` if no approved cases. Candidate reference_status = its package's.

### 6.8 Document requests (PRD §2, §10)
`refresh_document_requests(account|scenario)` (idempotent, deterministic `request_key`) generates:
D01 missing roster fields; D02 one per missing month in `M−11 … M` (M = latest confirmed billing month,
superseded invoices excluded); D03 one per member schedule lacking an approved version for an invoice period and
per missing `by_period` factor month; D04 one per account fact referenced by components but absent, plus scope
evidence for host_site/supplier accounts; D05 extra months for lookbacks beyond 11 months and a "documented
historical baseline (highest firm demand previously established)" per baseline input; D06/D08 when a package lacks
an approved reference case or tolerances are unset; D07 for scenarios missing candidate-specific summaries.
Uploading/confirming a fulfilling document marks requests `received`; Michael may `waive` with a reason.

### 6.9 Account readiness & enablement (R01, R04 "Account enablement", §8 release standard)
```text
AccountReadiness { configuration: {status: awaiting_setup|partial|configured, checks: [{code, status: pass|fail|unknown, detail}],
                   component_counts: {supported, awaiting_input, awaiting_implementation}},
                   calculation: {latest_billing_month, latest_analysis_id, coverage: complete|partial|blocked|none, uncovered_count},
                   validation: {package_reference_status, validated_components, pending, failed},
                   outstanding: {document_request_ids, awaiting_implementation_components},
                   enablement: {enabled, enabled_at, blockers: [code]} }
```
Configuration checks (owner step): `roster_fields_confirmed`, `scope_classified` (S12); `package_version_approved`,
`assignment_approved`, `versions_cover_invoice_periods`, `components_supported` (S14b); `invoice_labels_mapped`
(S15c); `reference_status_passed`, `no_open_d01_d04` (S20). Unimplemented checks report `unknown`.
`POST /accounts/{id}/enable` (reason) only when blockers = ∅; blockers include awaiting_input/
awaiting_implementation components, failing approved reference runs (`known_incorrect_calculation`), no passing
held-out case, open D01–D04 requests, scope not regulated_in_scope. Enablement lapses with a recorded event when a
blocker reappears. Non-enabled accounts appear as "awaiting setup" in the queue.

### 6.10 Source governance (PRD §9.3)
`authority` on documents; summaries never serve as a calculation basis (§6.1). `source_conflict` blocks the
affected component (status `blocked`, reason `source_conflict`) or candidate (`eligibility_conflict`) until
resolved with a governing source_ref + Michael's note. Re-fetching a URL whose bytes changed creates a new
document (new hash, same URL) and never overwrites the snapshot. `POST /documents/{id}/replace` creates a successor
document (`supersedes_id`) and emits `document.replaced`.

### 6.11 Comparison scenarios
Editing creates a new scenario version (copy of candidates and inputs); `GET /comparison-scenarios/{id}/diff/{other}`
lists changed assumptions/inputs/candidates. The API rejects `eligible`/`documented` without evidence refs
(`422 eligibility_evidence_required`). Runs are immutable and record scenario version, settings version and
package reference statuses. Comparison exports: CSV + printable report (§8).

### 6.12 Findings & account exceptions
- Transitions: any non-closed → any state with a `finding_event {from, to, reason}`; `→ closed` requires
  `outcome`; `→ accepted_below_threshold` requires the linked row to be unflagged in the referenced assessment
  (`409 not_below_threshold`); `→ credit_requested` requires `next_action`; `closed → needs_review` requires a
  reason. A potential discrepancy, a `billing_error_confirmation` and a `credit_receipt` (requires finding + document)
  are distinct records. Findings survive reruns (linked by account + billing_month + group_key).
- `account_exception` records recurring account-specific exceptions; on later reviews they surface as
  `awaiting_confirmation` explanation candidates and never auto-close flags.

---

## 7. Extraction & import (`tariff_app.extraction`, `tariff_app.synthetic`)

- `pdf_text.extract(bytes) -> [PageText{page_no, text, lines[{line_no, text, x0, top}], readable}]`; a page with
  < 20 non-whitespace characters is unreadable → document request "replace page or transcribe manually"
  (manual values get provenance `manual` and a source_ref with `quote=NULL` + transcription note).
- Spreadsheets (CSV/XLSX) normalize into `document_page` rows (one per sheet), lines = rows, locator `Sheet!A1`.
- **Synthetic PDF generator** (`tariff_app.synthetic.pdfgen`, S13): built-in Helvetica only, all text latin-1
  (raise otherwise; use `-` and `<=`, not `–`/`≤`), `set_creation_date(2026-01-01T00:00Z)` + fixed metadata →
  identical bytes for identical specs. One logical line per cell; fields separated by ` | ` (pdfplumber collapses
  multiple spaces). `render_document(title, pages)`, `render_invoice(spec)`.
- **Synthetic invoice grammar** (parsed by the heuristic extractor):
  `SYNTHETIC INVOICE - NOT A REAL BILL` · `Utility: <legal entity>` · `Invoice number: <x>` · `Account number: <x>` ·
  `Statement date: YYYY-MM-DD` · `Billing month: YYYY-MM` · `Service period: YYYY-MM-DD to YYYY-MM-DD` ·
  `Amount due: $1,234.56` · `CHARGES` rows `<label> | <qty> <unit> @ <rate> <rate_unit> | <signed $amount>` or
  `<label> | | <signed $amount>` · `DETERMINANTS` rows `<label>: <value> <unit>` · `BALANCE` rows
  `<label> | <signed $amount>`. `CR`, `(…)` and trailing `-` parse as negative.
- `InvoiceExtractor` protocol → `InvoiceProposal{fields (each value + locator + confidence), lines, determinants
  [{key, value, unit, period, locator}], warnings}`. `HeuristicInvoiceExtractor` (always available);
  `ClaudeInvoiceExtractor` (optional, S15e) uses structured output; values not found in the cited text are dropped.
- Billing-summary import (S15d): canonical columns `account_number, billing_month, key, value, unit[, period_start,
  period_end, note]` → preview (row errors: unknown key/unit, duplicates, conflicts) → confirm → input values
  (provenance reported, cell source_refs) feeding current months or history series.

---

## 8. API conventions (`/api/v1`)

- Routers: `meta, settings, research-candidates, facilities, accounts, roster, documents, source-refs,
  source-conflicts, tariff-schedules, tariff-versions, tariff-packages, assignments, mapping-rules,
  extraction-proposals, invoices, input-sets, billing-summaries, analyses, assessments, findings,
  reference-cases, document-requests, comparison-scenarios, portfolio, exports, views`.
- **Errors:** `tariff_app.api.errors.AppError(status, code, detail, context)`; handlers map AppError, Starlette
  HTTPException (`code="http_<status>"`) and RequestValidationError (`422 {detail: "Request validation failed",
  code: "validation_failed", context: {errors: [{loc, msg, type}]}}`) to `ErrorOut {detail, code, context}`.
  Every router declares `responses={400,404,409,413,422: {"model": ErrorOut}}`; OpenAPI post-processing removes
  `HTTPValidationError`.
- **Typed responses only:** JSON columns are re-validated into engine/API models on read; `dict[str, Any]`/`Any`
  in response models is forbidden (a test walks `app.openapi()` and fails on schemaless objects).
- Decimals are `DecimalStr`; enums are strings; state transitions are explicit POST actions (`/approve`,
  `/submit-for-review`, `/review`, `/supersede`, `/reject-duplicate`, `/reevaluate`, `/rerun`, `/enable`, `/confirm`).
- **Read models for screens (S24b):** `GET /views/analyses/{id}/review` (ReviewView: header {facility, utility legal
  entity, account, billing period/month, invoice version, package + resolved versions}, coverage + check scope +
  input requests, totals + bridge, rows with assessment flags + evidence, explanations + review statuses, findings,
  versions {analyses, assessments}), `GET /views/accounts/{id}/overview`, `GET /views/comparison-scenarios/{id}`,
  `GET /portfolio/queue`.
- **Exports:** `GET /exports/analyses/{id}.csv?assessment=`, `GET /exports/analyses/{id}/report?assessment=`,
  `GET /exports/comparison-runs/{id}.csv`, `GET /exports/comparison-runs/{id}/report`, reference-case fixture JSON.
  CSV = single RFC 4180 table with columns `section, key, label, status, billed, expected, difference, pct,
  flagged, check_scope, coverage, evidence_refs, value` (`section ∈ meta|row|total|bridge|finding|assumption`);
  numbers are exact canonical strings; meta row `display_rounding=none`.
- `make openapi` writes `frontend/src/api/openapi.json` (deterministic) and regenerates `schema.d.ts`;
  `make openapi-check` (temp-dir compare) is part of `make check` from S24b on.
- Single-process serving (S33): after API routers, `GET /{path:path}` serves files from the SPA dist or
  `index.html`; `api/` paths keep the JSON 404 envelope.

---

## 9. Frontend structure

Routes: `/` Portfolio queue · `/facilities`, `/facilities/:id` · `/accounts/:id` · `/roster/import` ·
`/research-candidates` · `/documents`, `/documents/:id` · `/invoices/:id` · `/tariffs`, `/tariffs/:scheduleId`,
`/tariff-versions/:id` · `/packages/:id` · `/analyses/:id` (review screen) · `/findings`, `/findings/:id` ·
`/comparisons`, `/comparisons/:id` · `/reference-cases` · `/settings`.

- `src/api/client.ts`: `createApiClient({ baseUrl = window.location.origin, fetch })` provided via `ApiProvider`;
  uploads via FormData `bodySerializer`; downloads via plain links.
- Tests: `src/test/mockApi.ts` `mockApi({'GET /api/v1/meta': () => fixture, ...})` returns a fetch stub injected
  through the provider (typed by `components['schemas']`); `renderWithProviders()` with `retry: false`.
- `src/lib/decimal.ts`: `formatDecimal(str, places)` / `formatPercent(ratioStr, places)` using BigInt string
  arithmetic, HALF_UP (`'1234.565'→'1,234.57'`, `'-0.005'→'-0.01'`). No `Number()`/`parseFloat` on amounts.
- Shared components: `Money` (exact tooltip + "rounded display" label), `Variance` (signed diff, pct to 2 dp, flag,
  "n/a (expected $0)"), `CoverageBadge`, `ProvenanceTag`, `StatusPill`, `SourcePassage`, `SyntheticBadge`,
  `VersionBanner`, `CheckScopeNotice`. The frontend never performs financial arithmetic.
- The review screen answers PRD §5's five questions in visible regions (header; coverage panel; totals + bridge;
  component table + evidence drawer; next actions).

---

## 10. Testing conventions

- Every behavior ships with tests in the same step; `make check` green before commit; budget ≤ 180 s on 4 CPUs
  (`pytest -n 4 --dist loadfile`); any test > 3 s uses the session template DB / seed template.
- Traceability: `@pytest.mark.req("ID", ...)` (backend) and `// req: ID` comments (frontend/E2E) with IDs from
  `docs/requirements.yaml`, each tagged with a `layer`. A conftest hook (from S10) rejects unknown IDs; the S24a
  meta-test statically scans backend tests (AST) for backend-layer coverage; S33 extends it to frontend/E2E.
- Expected values in tests and fixtures are **hand-derived** (comment or `derivation` text), never produced by
  calling the code under test. Fixture derivations are re-checked by an independent tiny Decimal evaluator.
- Hypothesis profile `default`: `deadline=None, max_examples=60, derandomize=True`; arithmetic-law properties use
  bounded strategies (`tests/strategies.py`).
- App tests use `tests/app/factories.py` builders (extended by each step, never duplicated) and a session-scoped
  template SQLite DB (upgrade once, copy per test). AI clients are injected and mocked; no network.

---

## 11. Fixture formats (binding for S07, S12, S14a, S20, S21, S23)

- `fixtures/synthetic/tariffs/<SCHEDULE>.json` = `{schedule: {code, name, kind, utility_legal_entity,
  service_territory, status, is_private, is_synthetic: true, description}, document: {title, authority, pages:
  [[line, ...], ...]} (latin-1), sources: [{ref_key, page_no, quote}], versions: [TariffVersionDef]}` — every
  `ref_key` used in versions is declared; every quote occurs (whitespace-normalized) on its page.
- `fixtures/synthetic/packages/<PKG>.json` = `{code, name, utility_legal_entity, service_territory, members:
  [{schedule_code, role, precedence, member_from, member_to, overrides}]}`.
- `fixtures/synthetic/reference_cases/<PKG>__<YYYY-MM>.json` = `{name, package_code, origin: synthetic_fixture,
  held_out, allocation, context: CalcContext, expected: {component_key: decimal}, expected_scope: {coverage,
  demand_determination}, derivation: {component_key: "<Decimal arithmetic expression>"}, reference_ids: [...]}`.
- `fixtures/synthetic/roster_20.csv` columns: `facility_code, facility_name, service_address, state,
  operating_status, utility_legal_name, account_number, meter_ids (;-separated), tariff_code, invoice_issuer,
  billing_arrangement, notes` (one row per account).
- `fixtures/synthetic/scenarios/fl_planned_2028.json` — six-candidate Florida scenario (shared + candidate-specific
  monthly inputs, history, eligibility facts, commitments, unmodeled conditions).
- `fixtures/synthetic/demo_manifest.json` (S23) — single source for seed, acceptance tests and E2E (entities by
  code; named cases: `rate_error`, `revised_bill`, `duplicate_upload`, `negative_credit`, `missing_rider_factor`,
  `billed_demand_only`, `cross_version`, `offsetting_lines`, `threshold_exact_1pct`, `threshold_above_1pct`).
- The S14a importer renders `document` with the S13 generator, uploads it, creates source_refs (validated),
  rewrites `ref_key`s to `src_` ids, creates DRAFT versions; approval is a separate call (seed/tests approve with
  reason "SYNTHETIC fixture approval").

---

## 12. Configuration

`tariff_app.config.Settings` (prefix `TARIFF_`): `DATA_DIR` (default `backend/.data`), `DATABASE_URL`
(default `sqlite:///<DATA_DIR>/tariff.sqlite3`), `AI_MODEL` (default `claude-opus-5-5`), `MAX_UPLOAD_MB` (25),
`SERVE_FRONTEND_DIST`. `ANTHROPIC_API_KEY` from env. The app lifespan runs `alembic upgrade head`.

## 13. Canonical commands

`make setup` · `make check` (lint + typecheck + unit tests; + `openapi-check` from S24b) · `make backend-test` ·
`make frontend-test` · `make fmt` · `make dev` · `make seed` · `make openapi` · `make e2e` · `make run` ·
`make traceability`.
