# Transfer-assignment benchmark summary

Scope: known configured FY2025-26 standalone annual reports only. This is not an unseen-report accuracy claim.

Overall status: **passed**
Expected records: 52
Extracted records: 52
Correct complete records: 52 / 52
Overall complete-record accuracy: 52 / 52 (100.00%)
Overall raw-value accuracy: 52 / 52 (100.00%)
Overall normalized-value accuracy: 52 / 52 (100.00%)
Overall unit accuracy: 52 / 52 (100.00%)
Overall canonical-metric accuracy: 52 / 52 (100.00%)
Overall provenance-field accuracy: 624 / 624 (100.00%)
Extraction runtime total: 0.200328 seconds

Accuracy denominators include all expected records; missing records are not removed.

| Lender | Status | Expected | Extracted | Correct | Missing | Unexpected | Duplicates | Runtime (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| bajaj | passed | 24 | 24 | 24 | 0 | 0 | 0 | 0.053610 |
  - Accuracy — complete: 24/24 (100.00%); raw: 24/24 (100.00%); normalized: 24/24 (100.00%); unit: 24/24 (100.00%); canonical: 24/24 (100.00%); provenance fields: 288/288 (100.00%)

Warnings:
- Subcolumn headings are not visible; positions are preserved only as column_1 and column_2 under each year, and their meaning is not inferred.
- The PDF text layer encodes the printed rupee sign as C in the ITFRupee font; raw values restore that font-specific glyph to ₹.
| chola | passed | 14 | 14 | 14 | 0 | 0 | 0 | 0.030976 |
  - Accuracy — complete: 14/14 (100.00%); raw: 14/14 (100.00%); normalized: 14/14 (100.00%); unit: 14/14 (100.00%); canonical: 14/14 (100.00%); provenance fields: 168/168 (100.00%)

Warnings:
- Weighted average maturity is kept distinct from Bajaj's weighted average residual maturity; cross-lender equivalence remains unresolved.
| ugro | passed | 14 | 14 | 14 | 0 | 0 | 0 | 0.115741 |
  - Accuracy — complete: 14/14 (100.00%); raw: 14/14 (100.00%); normalized: 14/14 (100.00%); unit: 14/14 (100.00%); canonical: 14/14 (100.00%); provenance fields: 168/168 (100.00%)

Warnings:
- Ugro's principal-outstanding amount is kept distinct from Bajaj/Chola loan amount assigned; definitions require review before aggregation.
- Ugro reports maturity and holding period in years; convert explicitly before comparison with month-denominated values. Ugro maturity is not Bajaj's residual maturity.
- Coverage is computed using only secured loans according to the source footnote; cross-lender comparability is limited.
- Non-Rated is preserved as source text and is not equated with Chola NA or Bajaj Unrated.
- The source footnote says this table excludes loans transferred through co-lending arrangements.

The source audit checks the configured source page, title, years, row labels, complete numeric tokens, units, page references, and both left/right value-column boundaries directly from the PDF. It does not call the extractor to create expected values.

Human visual review is still required for the final financial sign-off.
