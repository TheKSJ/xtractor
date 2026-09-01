# Learning guide

This guide explains the finance concepts, code paths and limits of the implemented Indian Lender Disclosure Intelligence System. It is written so that a reviewer can reproduce the work and defend the choices without treating the output as investment advice.

## 1. Transfer-through-assignment concepts

Loan assignment is the sale or transfer of an existing loan pool or receivable interest. The originator is the lender that originated the loan and transfers it; the assignee is the buyer or transferee; the borrower remains the party that owes the contractual cash flows. A transfer can release funding, manage concentration or capital, and recycle lending capacity, but the economics depend on servicing, recourse, retained interests and accounting treatment.

Principal outstanding is the balance owed by borrowers. Consideration received is the price paid by the assignee. They can differ because of provisions, discounts, timing and transaction terms, so the extractor keeps `loan_amount_assigned` and `aggregate_consideration_received` as separate metrics. Retention of beneficial economic interest (for example, MRR) is the percentage of the economic exposure retained by the originator; it is not the same as the cash consideration percentage.

Holding period is how long the transferor held an exposure before assignment. Contractual maturity is the original scheduled end of the loan. Residual maturity is the remaining time at the assignment date. A maturity row must not be combined with a residual-maturity row merely because both use months or years. Tangible-security coverage is a percentage for a defined secured population; the denominator must be known before it can be interpreted.

Co-lending exclusions matter because a lender may disclose its own on-book loans, co-lending loans, or both. Removing co-lending changes the population and can make an apparently similar amount non-comparable. The registry therefore records populations such as `loans_not_in_default`, `term_loans`, `advances` and `loans_not_in_default_excluding_co_lending` rather than silently standardising them.

## 2. Expected credit loss (ECL)

ECL is the present value of expected cash shortfalls, commonly represented by probability of default (PD), exposure at default (EAD), loss given default (LGD), recoveries and discounting. Stage 1 normally uses 12-month ECL. Stage 2 uses lifetime ECL after a significant increase in credit risk (SICR), while Stage 3 is credit-impaired and also uses lifetime ECL. The exact policy and thresholds remain lender-specific.

An ECL allowance reconciliation starts with an opening balance, adds or subtracts movements such as transfers between stages, new originations, repayments/derecognition, write-offs and same-stage or EAD changes, and ends at a closing balance. A transfer row describes an allowance movement in the destination stage column; it is not automatically a change in borrower count. A cure can move an exposure out of Stage 3, while a write-off removes an allowance/exposure under the lender's policy. Derecognition on sale is different from a write-off.

ECL can move differently from GNPA. GNPA is a gross non-performing-asset measure; ECL is an expected-loss allowance that responds to exposure mix, collateral, recoveries, PD/LGD assumptions, forward-looking scenarios and stage migration. A lower GNPA count does not prove that allowance should fall, and a higher allowance does not by itself prove that GNPA increased.

## 3. Implemented formulas

The analytical layer uses `Decimal` for calculations and emits decimal strings in JSON:

- `closing = opening + sum(additive movements)`
- `residual = opening + movement_total - closing`
- `absolute_change = current - prior`
- `percentage_change = (current - prior) / prior * 100` when the prior value is non-zero
- `INR crore = INR lakh / 100`
- `months = years * 12`
- `net transfer effect in Stage 2 or 3 = sum(transfer_to_stage_2 or transfer_to_stage_3 rows in that stage column)`

The reconciliation excludes explicit opening, closing and subtotal rows from the movement sum. Missing values remain unresolved; they are never replaced by zero. Ratios using AUM, gross loans or disbursements are not emitted because those denominators are not independently extracted and validated in this version.

## 4. How the code works

1. A YAML recipe names the report, source-manifest entry, page/index, table anchors, row y-coordinates and value-column bounds.
2. `lender_intel.ecl` verifies the SHA-256, page references, anchors and every configured cell, then writes raw text, normalized value, stage, row mapping and provenance.
3. `lender_intel.analysis` reconciles ECL movements and calculates only permitted period changes and transfer effects.
4. `lender_intel.comparability` compares definitions, units, populations, scopes and footnotes. A unit conversion is a mechanical status, not proof of economic comparability. An override needs a decision ID and rationale.
5. `lender_intel.reporting` writes JSON, CSV, Markdown and self-contained HTML. Every analyst-facing conclusion points back to a record ID and source page.

The legacy transfer extractor remains in place and its 52-record benchmark is unchanged. The new ECL family uses the same source-linked, configuration-driven approach without hard-coding report values in extraction code.

## 5. Worked examples

**Transfer assignment.** In the Mahindra holdout table, the source says consideration is to the extent of 80% of assets assigned and reports 20% retained beneficial economic interest. The system preserves the two rows and their units/labels; it does not infer that every lender's consideration is 80% or calculate a cross-lender retention ratio.

**ECL.** Bajaj FY2026 Stage 3 allowance is 2,859.31 crore versus 1,957.34 crore in the prior configured column. The mechanical change is 901.97 crore. The brief labels this an observed source value and mechanical calculation; it does not claim that a particular credit event caused the change.

## 6. Assumptions and limitations

- Coordinates and row labels are configured per report layout; a changed layout should fail validation and receive a new recipe.
- Canonical mappings are conservative. Ambiguous totals, POCI columns and combined source rows stay `unresolved` or low-confidence.
- Source populations, statement scope, exclusions and footnotes are not interchangeable by label alone.
- Results cover the configured Bajaj, Chola, UGRO and Mahindra documents. The 100% figures are known-report completeness/reconciliation results, not unseen-report accuracy.
- Annual-report PDFs are not committed; a reviewer must download the official source and verify the manifest hash.
- Human visual review of each cited page remains required before financial sign-off.

## 7. Interview questions and concise model answers

**Finance: Why are principal outstanding and consideration separate?** They are different economic quantities; consideration is a transaction price and may include discounts or provisions.

**Finance: Why is a unit conversion not enough?** INR lakh and INR crore can be converted mechanically, but populations, scope, timing and definitions can still differ.

**Finance: Why can ECL rise while GNPA falls?** ECL reflects expected loss assumptions and exposure mix, not only the gross NPA count.

**Finance: What does a Stage 2 transfer row mean?** It is an allowance movement reported in the Stage 2 column; it is not automatically a loan-count migration.

**Technical: How are silent extraction errors limited?** The recipe checks hashes, page/index agreement, anchors, cell boundaries, complete values, expected counts and stable IDs; failures are explicit.

**Technical: How are duplicate records handled?** The reporting loader deduplicates stable record IDs, while source extraction and benchmark validation retain expected denominators and flag count mismatches.

**Technical: Why use Decimal?** Financial arithmetic and residual comparisons should not depend on binary floating-point rounding.

**Technical: How are overrides controlled?** An override file must name the comparison ID and provide a rationale; the decision and warning are emitted with the matrix.

## 8. Glossary

`Assignee` — buyer/transferee of an assigned loan pool.

`Canonical metric` — a controlled analytical name linked to a source definition.

`Consideration` — transaction price paid for an assignment.

`Derecognition` — removal of an asset from the balance sheet under the applicable accounting criteria.

`EAD` — exposure at default.

`ECL` — expected credit loss allowance.

`GNPA` — gross non-performing assets.

`LGD` — loss given default.

`MRR` — minimum retention requirement/retained beneficial economic interest in the configured source wording.

`Originator` — lender that originated and transfers the exposure.

`POCI` — purchased or originated credit-impaired exposure; not automatically Stage 1, 2 or 3.

`Residual maturity` — remaining time until contractual maturity.

`SICR` — significant increase in credit risk, the usual Stage 2 trigger.

`Source column position` — stable physical column identifier retained even when the label is ambiguous.

`Reported nil` — a source dash or nil notation preserved as a distinct status, not an unreported value or an analyst-imputed zero.

Annual-report information may already be reflected in market prices. This descriptive dataset therefore cannot support stock-price prediction, causal claims or a composite risk score.
