# Cross-lender comparability review

The extractor stores what each report says. This review asks a separate
question: can two stored values safely be compared? A shared canonical name is
only a hypothesis until wording, unit, population and footnotes support it.

## Metric-by-lender review

| Lender | Original wording | Canonical metric | Source unit | Population / footnote | Status | Reason |
|---|---|---|---|---|---|---|
| Bajaj | Amount of loans transferred through assignment | `loan_amount_assigned` | INR crore | Loans not in default; two visible-value positions per year, but subcolumn headings are not visible | Unresolved | The amount concept is clear, but the two Bajaj positions must not be combined or given an invented meaning. |
| Chola | Amount of loan accounts assigned (₹ in crores) | `loan_amount_assigned` | INR crore | Loans not in default; one value per year | Unresolved | Related wording, but the Bajaj table has two unidentified positions and the aggregation/population needs review. |
| UGRO | Aggregate principal outstanding of loans transferred through assignment (Rs. in lakh) | `aggregate_principal_outstanding_loans_transferred_through_assignment` | INR lakh | Loans not in default; table footnote excludes co-lending transfers | Not comparable | Principal outstanding is deliberately kept separate from a generic amount assigned. |
| UGRO | Aggregate consideration received (Rs. in lakh) | `aggregate_consideration_received` | INR lakh | Same UGRO table and exclusions | Not comparable | Consideration received is a different economic measure from principal outstanding or loan amount assigned. |
| Chola | Count of loans accounts assigned | `loan_account_count_assigned` | count | Loans not in default | Comparable as a label only | No corresponding count row is present in Bajaj or UGRO's configured table. |
| Bajaj | Weighted average residual maturity | `weighted_average_residual_maturity` | months | Loans not in default; Bajaj wording is residual maturity | Not comparable to the other maturity fields | Residual maturity is not automatically the same as maturity. |
| Chola | Weighted average maturity (in months) | `weighted_average_maturity` | months | Loans not in default | Unresolved | The report does not, by this table alone, establish equivalence to Bajaj residual maturity. |
| UGRO | Weighted average Maturity of Loans (in years) | `weighted_average_maturity` | years | Loans not in default; co-lending excluded | Unresolved | It may be comparable to Chola after unit conversion, but the source definition/population still requires review. |
| Bajaj / Chola / UGRO | Weighted average holding period | `weighted_average_holding_period` | months / months / years | Source populations and UGRO exclusions apply | Comparable after unit conversion, subject to review | Years must be multiplied by 12 before comparison; source populations still need to be aligned. |
| Bajaj / Chola / UGRO | Retention of beneficial economic interest | `retention_beneficial_economic_interest` | percent | Each report's own transfer population | Comparable after scope review | All three report percentages, but the exact population and transfer perimeter must be checked before aggregation. |
| Bajaj / Chola / UGRO | Coverage of tangible security | `coverage_tangible_security` | percent / NA / percent | UGRO says coverage is calculated using secured loans; Chola prints `NA` | Not comparable | `NA` is missing/not reported, not zero; UGRO's stated calculation basis differs. |
| Bajaj / Chola / UGRO | Rating-wise distribution of rated loans | `rating_wise_distribution_rated_loans` | text / NA / text | Bajaj: `Unrated`; Chola: `NA`; UGRO: `Non-Rated` | Not comparable | These are distinct source texts and must not be collapsed into one category. |

The maturity separation is intentional. The RBI's transfer-of-loan framework
uses different wording for weighted average maturity and weighted average
residual tenor in different disclosure contexts; the project therefore does
not infer equivalence from similar words alone. See the [RBI Transfer of Loan
Exposures Directions](https://www.rbi.org.in/scripts/notificationuser.aspx/searchnew/scripts/scripts/NotificationUser.aspx?Id=12166).

## Explicit analytical conversions

The extracted JSON remains unchanged. A later analytical layer may calculate:

- `INR crore = INR lakh / 100`;
- `months = years * 12`.

For example, UGRO's FY2026 principal outstanding of `147,180.80` lakh is
`1,471.808` crore as a unit conversion only. It is still principal outstanding,
not automatically Chola/Bajaj loan amount assigned. UGRO's FY2026 maturity of
`6.27` years is `75.24` months mathematically, but it remains the UGRO
weighted-average-maturity concept—not Bajaj's residual-maturity concept.

## Evidence note

The three configured tables contain 52 extracted cells in total: 24 Bajaj,
14 Chola and 14 UGRO. The benchmark checks source labels, values, units,
positions and provenance for those configured reports. It does not establish
that the lenders' figures are economically interchangeable, nor does it
support a conclusion about credit quality, profitability or risk.
