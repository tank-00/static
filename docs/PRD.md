# Utility Tariff Interpreter — PRD v0.2 (Engineering Handoff)

> Converted verbatim from `Utility_Tariff_Interpreter_PRD_v0.2_Engineering_Handoff.docx`. This file is the requirements source of truth for the MVP. Section numbers and requirement IDs (R01–R12) are referenced throughout `docs/ARCHITECTURE.md` and `docs/PLAN.md`.

Version 0.2 \| October 14, 2026

# 1. Product definition

**Product promise:** Help an energy manager determine what a facility
should pay under its electricity tariff, explain each calculated charge,
and assess the cost and conditions of a small set of eligible tariff
options.

Primary workflow: Monthly invoice validation. Secondary workflow:
Bounded tariff comparison using confirmed candidates and explicit
operating scenarios. Subsequent expansion: Proposed-rate-change impact
analysis.

## Customer and discovery evidence

Michael Weizman, energy manager for Air Products' South region, is the
intended user and tariff reviewer. He described managing more than 20
utilities and different tariffs across sites. Cooperation is assumed for
product design; participation remains unconfirmed. \[D1\]

The engineering scope is approximately 20 facilities in one workspace.
Michael described monthly invoice review, lengthy tariff interpretation,
investigation of material differences and comparison of six tariffs for
a new site. Exact review time and invoice volume were unquantified.
\[D1\]

## V1 scope

| **Status**                  | **Scope**                                                                                                                                                                               |
|-----------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **User and portfolio**      | One user, Michael; one user type; approximately 20 facilities, with separate utility accounts where required. Michael approves inputs, tariff rules and reference calculations.         |
| **Validation and settings** | Invoice-based rate-application checking; editable calculation tolerances; default 1% investigation threshold, editable by the user. Public tariffs plus account-specific documents.     |
| **Deferred**                | Independent interval-data reconstruction; automatic tariff discovery; rate-case monitoring; additional commodities; procurement; enterprise integrations and multi-user administration. |

Deliverable: A saved, source-linked reconciliation showing expected and
billed charges, differences, coverage and follow-up notes; plus a
comparable-cost view for eligible tariffs. Utility contact, payments,
disputes and enrollment remain customer actions.

Configuration basis: Appendix A identifies 13 publicly documented
candidate locations across the southern U.S. Michael confirms the actual
approximately 20-facility roster, utility accounts and schedules. Public
site evidence stays separate from account configuration.

\[D1\] Michael Weizman interview, October 1: transcript lines 16, 82-94.
Product decisions: subsequent written confirmation in this conversation.

# 2. User journey and input contract

## Recurring monthly workflow

Michael creates or imports the facility/account roster, confirms the
applicable tariff package, and approves extracted rules. Each month he
uploads bills, resolves missing inputs, reviews component differences,
records a decision and exports supporting evidence. Approved
configurations carry forward with effective dates.

Every review is anchored to a specific account, billing period, invoice
version, tariff version and input set. A changed source or input creates
a new analysis version. Previously completed reviews retain their
original evidence.

## V1 validation basis

Rate-application check: Apply approved tariff rules to quantities
supplied on invoices, billing-detail statements or source-linked manual
entries. Display "Invoice-based check; underlying usage quantities
unverified" beside the result.

Where bills contain measured demand and sufficient history, derive
billing demand from those reported values. Where only billed demand is
available, check charges at that quantity and label demand determination
as unchecked. Independent meter-data reconstruction is deferred.

## Required and conditional inputs

| **Input**                      | **Requirement**                                                                                                                                                                                      |
|--------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Tariff package**             | Applicable base schedule, versions, referenced riders, monthly adjustment factors and service rules. Public sources are collected by the team; Michael confirms account applicability.               |
| **Invoices and history**       | Complete bills with all pages and line details. Request the latest bill plus the preceding 11 monthly bills; request additional history or a documented historical baseline when a rule requires it. |
| **Account and contract facts** | Service address, utility legal entity, tariff code, voltage, contract demand/capacity, meter basis, exemptions and special agreements where relevant.                                                |
| **Billing determinants**       | Invoice-reported kWh, measured/billed kW, time-of-use quantities, power factor/reactive quantities and demand history where available. Each value retains its source and meaning.                    |
| **Comparison scenario**        | Candidate tariffs, eligibility evidence, common period, explicit operating assumptions and candidate-specific billing determinants. Michael supplies or confirms them.                               |

## Readiness and completion

Keep calculation coverage (complete, partial or blocked), check scope,
review status and findings separate. "Complete" means complete coverage
of the stated rate-application basis. Underlying usage measurements
remain unverified.

Missing determinants, contract terms or rider versions generate specific
input requests. Available components remain usable as a labeled partial
result. Section 10 lists the documents to request; additional
requirements are generated from each tariff.

# 3. Requirements: intake and tariff interpretation

R01-R10 specify invoice validation; R11-R12 specify bounded comparison.
The public component inventory in Section 9 guides implementation across
the approximately 20-facility portfolio.

## R01. Facility and account workspace

Maintain approximately 20 facilities with one or more accounts each.
Store service address, utility legal entity, tariff assignment,
operating/planned status and billing arrangement. Michael is the sole
user. Public candidate sites require confirmation before becoming
configured accounts.

Acceptance: Import/edit a 20-facility roster; open each account's
documents, tariff versions, analyses and findings. Ambiguous invoice
assignment requests confirmation. Each account shows configuration and
calculation coverage independently.

## R02. Upload, extract and confirm

Support text-readable PDF invoices/tariffs and CSV/XLSX rosters or
billing summaries. Preserve originals and source-linked extracted
values. Allow corrections and manual entries with provenance. Unreadable
pages request replacement or manual transcription. Flag duplicate and
revised invoices. Document text supplies evidence; workflow changes
require explicit user actions.

Acceptance: Every input is inspectable and editable, including units and
dates. Distinguish measured demand, billed demand and contract demand.
Duplicate handling prevents accidental duplicate reviews; revised bills
explicitly supersede prior versions.

## R03. Structured tariff rules with evidence

Convert tariffs into reviewable components: formula, parameters, units,
required inputs, applicability, effective dates, rounding, dependencies
and exact source passage. Store legal utility entity, service territory
and tariff status. Linked riders and private amendments are individual
dependencies.

**Acceptance:** A reviewer can move from a calculated charge to the
applicable rule and exact supporting passage. Unresolved terms block the
affected component. The interface shows the component inventory and
which elements the engine supports.

## R04. Approval and version control

Michael approves rules and account applicability before a calculation is
finalized. Save edits, timestamp and reason. Reuse approved versions for
applicable periods. Rule changes require approval and identify affected
analyses for explicit rerun; historical reviews preserve their original
evidence.

**Acceptance:** A source or rule edit marks affected drafts as stale.
Completed analyses remain reproducible and unchanged. Every rerun
preserves the prior result and identifies the approved version used.

Account enablement: Assemble the base schedule, riders and relevant
private terms, map every invoice component, and record supported /
awaiting input / awaiting implementation status. An unresolved component
prevents complete coverage for that invoice.

# 4. Requirements: calculation and reconciliation

## R05. Reproducible calculation

Execute approved rules with deterministic arithmetic, explicit units and
tariff-specific precision/rounding. AI proposes extraction and
explanations. Validate dependencies and history first; distinguish
reported quantities from derived billing determinants. Missing inputs
produce partial or blocked results.

Acceptance: Identical versioned inputs reproduce identical amounts.
Michael establishes reference-calculation tolerances after sample
review; the user can edit them. Changes preserve tariff arithmetic and
raw differences. Unset tolerances show "Reference validation pending."
See Section 9.2.

## R06. Line-level and total reconciliation

Map invoice lines to expected components, including reviewed one-to-many
mappings. Show billed, expected, signed difference, check scope and
evidence. Reconcile comparable current-period charges, then bridge prior
balances, credits, payments and other balance items separately.

**Acceptance:** Show a full expected total only when its complete
calculation basis is covered. Partial results show a supported subtotal
and all uncovered components. Display line-level differences even when
positive and negative amounts cancel in aggregate.

Variance: signed difference = billed minus expected; percentage
difference = signed difference / absolute expected. Expected zero has no
percentage denominator. Use exact stored values for threshold evaluation
and clearly label rounded display values.

## R07. Investigation thresholds

Default investigation threshold: 1%, editable in Settings. Flag each
calculated component and a fully covered comparable total when absolute
percentage difference exceeds the threshold. Apply to both directions.
Show all differences. Exactly 1% is within the default threshold.

Acceptance: A nonzero billed amount against expected zero requires
review; both zero are matched. Missing coverage remains visible. Saving
a threshold change creates a new assessment version on explicit
reevaluation; previous reports retain their saved setting. No dollar
threshold is required in V1.

## R08. Explanation and review decision

Explain differences using the recorded tariff, invoice and inputs. Mark
explanations documented, awaiting Michael's confirmation or unresolved.
Save Michael's conclusion and notes. Finalizing a review preserves the
calculation scope, coverage and any open findings.

**Acceptance:** Every explanation links to the relevant tariff rule,
invoice line or input. Unsupported causal claims are omitted. Numerical
calculation tolerance and business investigation materiality remain
separate settings.

# 5. Requirements: findings and evidence

## R09. Finding lifecycle and follow-up

Michael can save a finding for any difference or missing item. Store
amount, account, period, evidence, notes and next action. States: Needs
review, Explained, Accepted below threshold, Utility inquiry, Credit
requested, Closed. Michael owns all findings; assignment controls are
omitted.

**Acceptance:** The user can review the invoice and leave a follow-up
finding open. A potential discrepancy, customer-confirmed billing error
and received credit have distinct records. Any received credit links to
supporting documentation and the underlying finding.

## R10. Evidence export

Provide a CSV reconciliation and a printable evidence report. Include
analysis ID, account/period, input/rule/engine versions, check scope,
coverage, values and differences, threshold/tolerance settings,
assumptions, source references, review decision and findings.

**Acceptance:** Exported numbers match the saved analysis. Partial
coverage, scenario estimates and unresolved issues remain visible in
exports. A report generated from an older analysis is clearly versioned.
External submission remains a deliberate customer action.

## What the main review screen must answer

| **Question**              | **Required display**                                                                                             |
|---------------------------|------------------------------------------------------------------------------------------------------------------|
| **What am I reviewing?**  | Facility, legal utility entity, account, billing dates, invoice version and applicable tariff package.           |
| **How complete is this?** | Complete / partial / blocked coverage; invoice-based quantity notice; unchecked determinants and missing inputs. |
| **What should we pay?**   | Comparable billed and modeled charges; supported subtotal for a partial result; separate balance-item bridge.    |
| **Why is it different?**  | Component calculations, signed differences, 1% or edited threshold, source passages and documented explanations. |
| **What happens next?**    | Source-linked corrections, input requests, Michael's decision, saved follow-up and evidence export.              |

## Review states

Analysis workflow: Draft -\> Ready for review -\> Reviewed. A saved
calculation with identified scope and gaps can enter review. Partial
coverage requires explicit acknowledgment. Source/input changes create a
new draft; threshold changes create a new assessment version. Findings
remain independently open.

A completed review confirms that the user examined the saved analysis
and recorded a conclusion. Product labels must preserve the precise
scope of the checks performed and any remaining investigation.

# 6. Requirements: bounded tariff comparison

Michael described six eligible options for a new site. Support a
six-candidate comparison in one scenario; candidate count remains
configurable. Eligibility and required operating conditions are
explicit. \[D1\]

## R11. Candidate set and operating scenario

Michael selects a facility, supplies or confirms candidate tariffs, and
records eligibility evidence. Capture a common time horizon and
operating scenario. Candidate status is Unconfirmed, Eligible or
Ineligible; existing-account grandfathering is recorded separately from
new enrollment eligibility.

Acceptance: Missing or conflicting eligibility evidence prevents a
candidate entering the eligible-cost ranking. Michael can save baseline
and edited scenarios; changed assumptions are visible. A
closed-to-new-business tariff requires documented existing eligibility
for its permitted use.

## R12. Comparable cost and conditions

Use each approved tariff to calculate costs on the same scenario. Show
components, modeled difference from a chosen baseline, commitments and
unmodeled conditions. Rank only fully modeled, eligible candidates. A
cheapest label names its scenario, horizon and candidate set.

Acceptance: Each determinant is compatible with that candidate's
definitions. A flat monthly total cannot supply missing on-peak
quantities. Request candidate-specific summaries or explicit scenario
assumptions; preserve partial status until supplied. User assumptions
label the result as a scenario estimate.

## Comparison output

| **Field**                  | **Purpose**                                                                                                                       |
|----------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| **Tariff and eligibility** | Schedule/version, utility entity, territory, entry restrictions, Michael's confirmation and supporting conditions.                |
| **Modeled cost**           | Comparable component totals, covered period, baseline difference and calculation coverage.                                        |
| **Operating assumptions**  | Shared facility scenario; separate candidate-specific determinants where definitions differ; all manually assumed values labeled. |
| **Decision evidence**      | Sources, cost drivers, documented operating commitments and unresolved considerations.                                            |

Annual scenarios: Require 12 monthly input sets or an explicit
Michael-approved forecast with assumptions. Reusing a single month for
an annual estimate requires visible approval of that assumption.
Expected savings remain modeled estimates.

Enable comparison for each candidate after its calculation rules pass
reference tests. Planned facilities support scenario analysis before
invoices exist. V1 uses invoice summaries and user inputs; independent
interval simulation remains deferred.

\[D1\] Interview transcript, lines 22, 68-78 and 90. Public eligibility
examples appear in Section 9.

# 7. Product structure and data model

## Core workspaces

Portfolio queue: Monthly invoices across approximately 20 facilities.
Filter by facility, utility, period, coverage, review state and
threshold flag. Show accounts awaiting setup alongside configured
accounts. Public research candidates remain separately labeled until
confirmed.

Facility: Account details, documents, applicable rules and history.
Invoice review: Component reconciliation with adjacent source evidence.
Comparison: Candidates, assumptions and results. Settings: Editable
investigation threshold and reference tolerances, with saved versions.

## Minimum persistent objects

| **Object**                | **Minimum information**                                                                                                                                 |
|---------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Workspace / settings**  | One user: Michael. Investigation threshold defaults to 0.01; reference tolerances start unset. Record settings versions and change history.             |
| **Facility / account**    | Stable IDs, service address, utility legal entity, account/meter ID, tariff package, billing arrangement, operating status, confirmed/candidate status. |
| **Source document**       | Original file, content hash, type, source URL if public, publication/effective dates, retrieval/upload time, extraction and source locations.           |
| **Input set**             | Value, unit, period, semantic type, reported/derived/assumed provenance, source pointer and correction history. Null represents missing data.           |
| **Tariff rule version**   | Component expressions, dependencies, precision, applicability, source references, effective interval, account-specific overrides and Michael approval.  |
| **Analysis / assessment** | Account/period; invoice, input, tariff and engine versions; component results/coverage; settings snapshot; saved decision and superseded-version link.  |
| **Finding**               | Affected component, difference, explanation, lifecycle, notes, evidence and documented outcome. Owner fixed to Michael.                                 |
| **Comparison scenario**   | Candidate set, eligibility, common horizon, shared and candidate-specific inputs, assumption provenance, baseline and saved run versions.               |

## Processing sequence

Preserve sources -\> extract -\> confirm account and dependencies -\>
approve rules -\> calculate -\> reconcile -\> explain from evidence -\>
Michael reviews -\> save/export. Resolve the dependency graph before
numerical execution.

Rule/source changes mark drafts stale. Completed runs remain immutable;
reruns preserve prior versions. Settings-only changes reuse saved
amounts and create a new assessment snapshot. Safe retries preserve
uploads and avoid duplicate analyses.

# 8. Quality and exception handling

## Required acceptance-test matrix

| **Test**                    | **Expected outcome**                                                                                                                                        |
|-----------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Reference calculation**   | Supported components reproduce Michael-approved expected values within configured tolerances; arithmetic and rounding tests also pass.                      |
| **Threshold boundary**      | Expected \$100,000: billed \$101,000 is at 1%; \$101,001 triggers; \$98,999 triggers in the opposite direction. Preserve signed differences.                |
| **Zero and missing values** | Expected zero / billed nonzero requires review. Missing expected amount is partial, distinct from zero. Empty required fields remain unresolved.            |
| **Missing rider / history** | Identify the specific missing source or historical determinant. Show only supported subtotals until the dependency is resolved.                             |
| **Dates and applicability** | Select the source effective for the account and period. Cross-version bills require an approved allocation rule. Wrong utility/territory blocks use.        |
| **Quantity provenance**     | Invoice-based checks retain the unverified-usage label; billed demand alone leaves demand determination unchecked. Manual assumptions remain visible.       |
| **Versioning and exports**  | Corrections create new versions; historical reports remain reproducible. Exported figures, limitations and settings match the selected saved analysis.      |
| **Comparison and offsets**  | Exclude unresolved candidates from ranking. Missing TOU quantities remain explicit. Opposite line differences remain visible despite a zero net difference. |
| **Portfolio and settings**  | One user can review 20 distinct facilities and six tariff candidates. Editing the 1% threshold or tolerance preserves previous assessment versions.         |

## Additional reference cases

Include a seasonal demand rule, demand-dependent energy block, minimum
bill, voltage/loss adjustment, historical ratchet, variable rider and
ineligible new-enrollment candidate. Test negative credits and
duplicate/revised bills. Use clearly labeled synthetic cases before
customer documents arrive.

Synthetic examples establish software behavior. Michael's worked bills
establish account-specific correctness. Store reference amounts, inputs,
expected scope and source versions for each test; include a held-out
billing period after rule setup.

## Release quality standard

A complete result requires approved rules and all inputs for its stated
calculation basis. A reviewed partial result retains every limitation.
Known incorrect calculations or unsupported complete labels block
account enablement. Business KPIs and rollout milestones are outside
this handoff.

# 9. Public tariff implementation inventory

These official utility documents establish concrete rule patterns for
development. Actual account assignment requires Michael's invoice and
contract confirmation. Each enabled account needs its complete
applicable package, including historical versions. Source IDs link to
Appendix B.

<table>
<colgroup>
<col style="width: 30%" />
<col style="width: 69%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Source and applicability</strong></th>
<th><strong>Calculation patterns to implement</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td><strong>T1 | Georgia Power PLL-19<br />
Effective June 2026</strong></td>
<td>Demand-dependent energy blocks; seasonal demand ratchets; contract
floors; minimum bill; reactive-demand charge; linked cost-recovery
schedules. Requires billed determinants, prior-period demand and
contract values.</td>
</tr>
<tr class="even">
<td><strong>T2 | Entergy Louisiana LIPS-L<br />
Effective August 30, 2024</strong></td>
<td>Demand tiers, energy, reactive charge, fuel adjustment,
facilities/loss terms and contract minimum. Historical demand floors
include an earlier all-time peak. Legacy service-area and
closed-to-new-business restrictions require evidence.</td>
</tr>
<tr class="odd">
<td><strong>T3 | Entergy Louisiana LPS-G<br />
Effective August 30, 2024</strong></td>
<td>Seasonal demand pricing, fuel adjustment, voltage credit/charge,
transformer losses, power-factor treatment, off-peak adjustment and
contract-linked demand floors. Use each rule's specified quantity
basis.</td>
</tr>
<tr class="even">
<td><strong>T4 | FPL business rate summary<br />
Effective October 2026</strong></td>
<td>Fixed, demand and TOU energy charges; fuel, conservation, capacity,
environmental and storm components; credits and curtailment terms.
Summary is an inventory aid. Approved tariff sheets and rider terms are
required for calculations.</td>
</tr>
<tr class="odd">
<td><strong>T5 | Georgia Power TOU-HLF-17<br />
Effective June 2026</strong></td>
<td>TOU and eligibility test case. Its commercial multi-account
conditions apply to that utility/rate. The portfolio's approximately 20
facilities alone establish no eligibility. Capture all stated conditions
before comparison.</td>
</tr>
</tbody>
</table>

## Coverage across the full portfolio

Implement the common rule families below and register per-account
components as Michael's roster arrives. Retain every confirmed facility
in the workspace, including accounts awaiting documents or rule support.
Publicly documented company sites are research candidates; their serving
utilities and rate codes stay blank until confirmed.

A private supplier contract, host-site charge or other billing
arrangement requires its own applicable evidence. Keep that account
visible with an explicit scope classification. Regulated electricity
remains the V1 calculation scope; broader supply-contract modeling is
deferred.

# 9.1 Rule primitives and source handling

| **Primitive**                      | **Required behavior**                                                                                                                                                                |
|------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Fixed and linear charges**       | Fixed currency amounts and rate multiplied by quantity; preserve cents/kWh versus dollars/kWh and kW versus kWh. Store exact decimal rates.                                          |
| **Tiers and demand-linked blocks** | Progressive bands with open-ended final tiers; boundaries may depend on billing demand. Allocate each quantity once and retain intermediate subtotals.                               |
| **Calendar and conditional rules** | Effective intervals, billing-month seasons, service voltage and tariff-specific conditions. TOU quantities must be supplied for the candidate's defined windows.                     |
| **History and demand floors**      | Lookback windows, documented historical maxima, contract floors and maximum/minimum operations. Distinguish measured, billed, firm and contract demand; avoid circular dependencies. |
| **Adjustments and minimum bills**  | Ordered calculations, explicit percentage bases, losses, credits, reactive charges and minimum bill comparisons. Dependencies determine which subtotal each adjustment uses.         |
| **Dependencies and precedence**    | Base schedule, riders, monthly factors and private amendments have independent versions. Michael resolves conflicts; save the supported precedence and reasoning.                    |

## 9.2 Editable reference tolerances

Store an absolute-dollar tolerance and an optional relative tolerance
for reference comparisons, with component overrides where required.
Michael sets initial values after reviewing sample calculations. The UI
allows edits; unset values retain a pending reference-validation status.

Pass rule: absolute(calculated - reference) \<= max(absolute tolerance,
relative tolerance x absolute(reference)); omit an unset term. At
reference zero, use the absolute term. Tolerances govern reference-test
classification. They preserve the source-prescribed arithmetic and the
separate 1% invoice investigation rule.

## 9.3 Source package completeness

For every dependency retain document/version, effective interval, source
page or table cell, required inputs and support state. A mutable public
URL requires a saved document snapshot. A newer schedule applies only to
its approved effective period. Conflicting summaries require the
governing tariff and Michael's review.

# 10. Documents to request from Michael

Request a portfolio-level package covering approximately 20 facilities.
Public materials supply research and base documents; Michael supplies
the actual account roster, private terms and billing evidence. A usable
first batch is the roster, latest complete bill for each account and one
demonstrated reconciliation.

<table>
<colgroup>
<col style="width: 29%" />
<col style="width: 70%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Request</strong></th>
<th><strong>Exact contents and purpose</strong></th>
</tr>
</thead>
<tbody>
<tr class="odd">
<td><strong>D01 | Facility/account roster<br />
Required for all facilities</strong></td>
<td>Spreadsheet with internal facility name/ID, service address, state,
operating or planned status, utility legal name, account and meter IDs,
tariff code, and who issues the power invoice. Include every electricity
account at each site.</td>
</tr>
<tr class="even">
<td><strong>D02 | Complete bills<br />
Required for each account</strong></td>
<td>Latest invoice plus preceding 11 consecutive monthly invoices, all
pages and billing-detail attachments. Include corrected bills and
relevant credits. PDF preferred; accompanying spreadsheets are useful.
Establish quantities, rate labels, historical demand and balance
items.</td>
</tr>
<tr class="odd">
<td><strong>D03 | Applicable tariff package<br />
Team retrieves; Michael confirms</strong></td>
<td>Base schedule; versions effective during the supplied bills;
referenced riders and service rules; monthly fuel/other adjustment
factors. Ask Michael for existing links/files and confirmation of
applicable options and amendments.</td>
</tr>
<tr class="even">
<td><strong>D04 | Account/service terms<br />
Required when they affect charges</strong></td>
<td>Electric service agreement and amendments; contract demand/capacity,
voltage and metering/loss basis; minimum bills; special
discounts/exemptions; curtailment/load-control terms; facilities
charges. Include host-site or supplier arrangements for scope
classification.</td>
</tr>
<tr class="odd">
<td><strong>D05 | Billing determinants/history<br />
Conditional on tariff</strong></td>
<td>Billing-detail statements or utility summaries showing measured and
billed demand, TOU quantities, reactive/power-factor values and
historical baselines absent from bills. Request older maximum-demand
evidence where required. Independent interval usage is outside the
starting assumption.</td>
</tr>
</tbody>
</table>

## Why the history request is conditional

Entergy LIPS-L includes a floor tied to the highest firm demand
previously established. A 12-month bill pack may therefore need an
additional documented historical baseline. Where only billed demand is
available, the UI can check rates at that value while identifying the
unresolved demand determination. \[T2\]

# 10.1 Reference examples and Michael demo

| **Request**                         | **Exact contents and purpose**                                                                                                                                                                                                                                             |
|-------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **D06 \| Worked reference reviews** | Existing spreadsheets, notes or calculations for a representative bill under each distinct tariff package; explanation of expected component values, rounding and material differences. Include one known discrepancy or amended bill where available.                     |
| **D07 \| Comparison example**       | The six tariffs discussed for the Florida project, utility correspondence confirming eligibility, and a shared operating scenario. Request expected monthly consumption, candidate-specific demand/TOU summaries, voltage, operating flexibility and relevant commitments. |
| **D08 \| Configurable settings**    | Michael's reference tolerances after sample review. Investigation threshold starts at 1%; he can edit it directly. Record what each setting means and preserve changes in assessment history.                                                                              |

## Demo sequence

1\. Michael opens the facility/account list and identifies the legal
utility entity, account, tariff and relevant contract terms for one
site.

2\. He reviews a real monthly invoice, shows the quantities and clauses
he uses, explains how he handles riders/history, and walks through his
expected charge calculation.

3\. He explains one difference, the action he would take and the
evidence needed to contact the utility. Capture any recurring
account-specific exception.

4\. He compares the Florida candidates using the same site scenario,
explains eligibility and operating trade-offs, and confirms the inputs
needed to make each cost comparable.

5\. He reviews the interpreted rules, source links and reference
results, then sets tolerances. Save the resulting example as a versioned
acceptance fixture.

## Current-workflow baseline

The interview supports a recurring monthly review across many
utility/tariff combinations, manual interpretation of long documents,
materiality-based escalation and occasional multi-tariff analysis. It
supplies no measured minutes per bill, spend, error incidence or
recovery rate. \[D1\]

## Engineering completion checks

The 20-facility workflow, input correction, approved-rule calculation,
1% flagging, version history, evidence export and six-candidate scenario
flow are demonstrable. Each account separately records which
calculations Michael has validated and which documents or
implementations remain outstanding. Commercial terms, staffing, dates
and service-level targets are outside this document.

# Appendix A. Air Products location research

Public evidence identifies 13 candidate locations across seven southern
states. The selection supports portfolio discovery and test-case
diversity. Michael's approximately 20-facility roster remains the
account source of truth; inclusion in his remit, current operation and
serving utility require confirmation. Research date: October 14, 2026.

| **Candidate location**         | **Public evidence**                                                                                                                         | **Product implication**                                                                                          |
|--------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| **Orlando, Florida**           | Air Products' 2026 Cocoa announcement describes its existing Orlando air separation unit. \[S1\]                                            | Candidate for monthly review after account confirmation.                                                         |
| **Cocoa, Florida**             | April 2026 announcement targets a new air separation unit for the second half of 2028. \[S1\]                                               | Planned-site comparison example. Possible match to the interview's Space Coast site; Michael confirms.           |
| **Conyers, Georgia**           | July 2024 announcement identifies an existing site receiving a new/replacement air separation unit. \[S2\]                                  | Confirm actual operating unit, account and tariff; keep construction and operating bills distinct.               |
| **Reidsville, North Carolina** | Same announcement identifies the existing Reidsville site and planned replacement capacity. \[S2\]                                          | Candidate for monthly review; utility entity and operating-unit status require confirmation.                     |
| **Decatur, Alabama**           | Air Products food-safety certificate identifies liquid oxygen/nitrogen operations at 75 Ipsco Street NW. Certificate issued in 2026. \[S3\] | Candidate for monthly review. Obtain the actual utility invoice before tariff selection.                         |
| **Kingsport, Tennessee**       | 2018 announcement identifies existing supply operations at Eastman and new replacement air separation units. \[S4\]                         | Historical site evidence. Confirm current operations and whether electricity is billed directly or via the host. |
| **Geismar, Louisiana**         | 2020 announcement reports a new gases plant onstream supplying Huntsman. \[S5\]                                                             | Confirm the utility account and any host-site power/service agreement.                                           |

Research classification: Company announcements and certificates
establish site evidence at their stated dates. Future start-up targets
retain planned status until independently confirmed. Public company
documents establish no account number, rate assignment or
Michael-specific ownership.

# Appendix A. Candidate locations continued

| **Candidate location** | **Public evidence**                                                                                                                         | **Product implication**                                                                                       |
|------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| **Convent, Louisiana** | Company release documents a hydrogen plant onstream in 2006. \[S6\]                                                                         | Historical candidate; confirm current inclusion and electricity billing arrangement.                          |
| **La Porte, Texas**    | Air Products food-safety certificate lists 10202 Strang Road and liquid oxygen/nitrogen operations. Issued 2025, valid through 2028. \[S7\] | Confirm facility boundary and accounts; several production units can share one site.                          |
| **Baytown, Texas**     | Company release documents hydrogen production; separate company certification identifies an air separation location. \[S6, S8\]             | Confirm whether these are separate facilities/accounts in Michael's roster; avoid duplicate site assumptions. |
| **Midlothian, Texas**  | Company certificate lists liquid oxygen/nitrogen operations at 310 Ward Road. Issued 2025, valid through 2028. \[S9\]                       | Candidate for monthly review; identify the actual electricity supplier and contract basis.                    |
| **Port Arthur, Texas** | Company carbon-capture page describes two Air Products hydrogen reformers within the Valero refinery. \[S10\]                               | Host-site setting; confirm electric billing and relevant private terms.                                       |
| **Texas City, Texas**  | 2023 announcement identifies a planned carbon monoxide facility with a 2026 target. \[S11\]                                                 | Project evidence; confirm commissioning and actual account before treating as an operating bill-review site.  |

## Portfolio implications

Air Products reported 18 operating industrial-gas facilities in
Louisiana in June 2026. This company-wide count reinforces the need to
request the actual roster. The same release discontinued the Louisiana
Clean Energy Complex; that project is excluded from the candidate list.
\[S12\]

Engineering must keep facility, production unit, utility account and
tariff assignment as separate concepts. Geography can guide document
research. Account documents establish the legal utility entity and
actual service territory; host billing and supplier contracts require
separate classification.

Regional lookup leads: Florida utility tariffs; Georgia Power schedules;
the relevant North Carolina, Alabama and Tennessee utility schedules;
Louisiana industrial rates; and the documented Texas account
arrangements. These are research routes, with no utility-to-facility
assignments implied.

# Appendix B. Source register

Linked titles open the public source. Preserve downloaded tariff
versions during implementation; live URLs can change. Product
requirements are specified in Sections 1-10. Public sources support the
named examples and location evidence.

## Discovery and utility documents

\[D1\] Uploaded Michael Weizman interview, October 1, "Pasted text.txt":
role at line 16; utility portfolio and monthly reviews at lines 82-94;
tariff comparison at line 90. Scope/settings confirmed in the subsequent
conversation.

**\[T1\]** [<u>Georgia Power: Power and Light Large,
PLL-19</u>](https://www.georgiapower.com/content/dam/georgia-power/pdfs/tariffs/pll.pdf).
Effective June 2026. Pages 1-3: blocks, demand, minimums, adjustments
and applicability.

**\[T2\]** [<u>Entergy Louisiana: Large Industrial Power Service,
LIPS-L</u>](https://www.entergylouisiana.com/wp-content/uploads/ell_elec_lips-l.pdf).
Revision 2; August 30, 2024. Pages 1-3: terms, charges, dependencies and
history.

**\[T3\]** [<u>Entergy Louisiana: Large Power Service,
LPS-G</u>](https://www.entergylouisiana.com/wp-content/uploads/ell_elec_lps-g.pdf).
Revision 1; August 30, 2024. Pages 1-4: charges, voltage, demand and
contract terms.

**\[T4\]** [<u>Florida Power & Light: Business rates and
clauses</u>](https://www.fpl.com/content/dam/fplgp/us/en/rates/pdf/business-rates-oct2026.pdf).
October 2026 summary. Pages 1-2. Obtain governing tariff sheets and
referenced riders before enabling calculations.

**\[T5\]** [<u>Georgia Power: Time of Use - High Load Factor,
TOU-HLF-17</u>](https://www.georgiapower.com/content/dam/georgia-power/pdfs/tariffs/tou-hlf.pdf).
Effective June 2026. Pages 1-2. Eligibility/TOU test example;
applicability requires account evidence.

## Company site evidence: Southeast

**\[S1\]** [<u>Air Products: New air separation unit in Cocoa,
Florida</u>](https://www.airproducts.com/company/news-center/2026/04/0424-air-products-build-own-operate-new-air-separation-unit-florida).
April 24, 2026. Cocoa project and existing Orlando operation.

**\[S2\]** [<u>Air Products: New air separation units in Georgia and
North
Carolina</u>](https://www.airproducts.com/company/news-center/2024/07/0717-air-products-new-air-separation-units-in-georgia-and-north-carolina).
July 17, 2024. Conyers and Reidsville existing locations.

**\[S3\]** [<u>Air Products: Decatur food-safety
certificate</u>](https://www.airproducts.com/-/media/files/external/iso/fssc-22000/food-safety-fssc22000-decatur-us.pdf).
2026 certificate. Site location and production scope.

# Appendix B. Source register continued

**\[S4\]** [<u>Air Products: Two air separation units for Eastman,
Kingsport</u>](https://www.airproducts.com/company/news-center/2018/10/1031-air-products-to-build-own-operate-two-new-asu-for-eastman-coal-gasification).
October 31, 2018. Historical site/replacement project evidence.

**\[S5\]** [<u>Air Products: Geismar gases plant onstream, supplying
Huntsman</u>](https://www.airproducts.com/company/news-center/2020/07/0701-air-products-new-gases-plant-in-louisiana-onstream-and-supplying-huntsman).
July 1, 2020. Operational milestone and host-site context.

**\[S6\]** [<u>Air Products: Convent and Baytown hydrogen facilities
onstream</u>](https://investors.airproducts.com/news-releases/news-release-details/air-products-convent-louisiana-and-baytown-texas-hydrogen).
January 26, 2006. Historical location evidence; current roster
confirmation required.

**\[S7\]** [<u>Air Products: La Porte food-safety
certificate</u>](https://www.airproducts.com/-/media/files/external/iso/fssc-22000/food-safety-fssc2200-laporte-us-certificate.pdf).
August 21, 2025; valid through November 2028. Site and production scope.

**\[S8\]** [<u>Air Products: Baytown food-safety
certificate</u>](https://www.airproducts.com/-/media/files/external/iso/fssc-22000/food-safety-fssc2200-baytown-us-certificate.pdf).
2024 certificate with 2025 expiry. Historical air-separation site
evidence; current status requires confirmation.

**\[S9\]** [<u>Air Products: Midlothian food-safety
certificate</u>](https://www.airproducts.com/-/media/files/external/iso/fssc-22000/food-safety-fssc22000-midlothian-us-certificate.pdf).
August 8, 2025; valid through September 2028. Site and production scope.

**\[S10\]** [<u>Air Products: Carbon capture at Port
Arthur</u>](https://www.airproducts.com/company/innovation/carbon-capture).
Company description of the two reformers at the Valero refinery. Other
projects on this page require separately dated status checks.

**\[S11\]** [<u>Air Products: Two Gulf Coast carbon monoxide
projects</u>](https://www.airproducts.com/company/news-center/2023/05/0508-air-products-to-expand-worlds-largest-carbon-monoxide-pipeline-network-in-us-gulf-coast).
May 8, 2023. La Porte/Texas City project evidence; planned start dates
require confirmation.

**\[S12\]** [<u>Air Products: Louisiana Clean Energy Complex
discontinued</u>](https://www.airproducts.com/company/news-center/2026/06/0630-air-products-not-proceeding-louisiana-3rd-q-finalizing-with-yara-neom-renewable-ammonia-sa).
June 30, 2026. Eighteen operating Louisiana facilities and cancelled
LCEC project.
