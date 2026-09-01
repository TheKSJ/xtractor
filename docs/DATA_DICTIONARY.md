# Data dictionary

Each extraction result is a JSON object with `metadata`, `records`, `warnings`,
`warning_details` and `validation_summary`. Analyst bundles add JSON/CSV reports
around the same records; raw records are not overwritten by analysis.

| Field | Meaning |
|---|---|
| `record_id` | Stable source/row/year/column identity used by calculations and review. |
| `disclosure_family` | `transfer_assignment` or `ecl_stage_movement`. |
| `company`, `reporting_period`, `statement_scope` | Document identity and standalone/consolidated scope. |
| `source_document_id` | Key in `config/source_manifest.yaml`. |
| `source_filename`, `source_file`, `source_manifest_file` | Exact source and manifest references. |
| `pdf_page_number`, `pdf_page_index`, `printed_page_number` | One-based PDF page, zero-based library index and page printed in the report. |
| `note`, `table_title` | Note number and original table title. |
| `original_row_label` | Source wording, retained for audit. |
| `canonical_metric` | Controlled transfer-assignment metric name. |
| `canonical_movement_category` | Controlled ECL movement name, or `unresolved_movement`. |
| `original_stage_label`, `canonical_stage` | Source stage wording and `Stage 1`, `Stage 2`, `Stage 3` or `unresolved`. |
| `row_role` | ECL `opening`, `movement`, `subtotal` or `closing`. |
| `year` | Reporting year represented by the source column/row. |
| `source_column_position` | Stable physical column identifier; ambiguous columns are not merged. |
| `raw_value` | Text read from the PDF cell, including parentheses, dashes, `NA`, `Unrated` or `Non-Rated`. |
| `normalized_value` | Numeric value, source text, or `null` where the source explicitly reports not applicable/missing. |
| `unit` | `INR_crore`, `INR_lakh`, `months`, `years`, `percent`, `count` or `text`. |
| `value_status` | `reported_value`, `reported_nil` or `reported_text` where supplied. |
| `population_scope` | Portfolio definition used by the source table. |
| `mapping_confidence`, `mapping_explanation` | Confidence and reason for any canonical mapping. |
| `source_footnotes` | Footnotes/exclusions that affect interpretation. |
| `extraction_warnings` | Record-level validation or semantic warnings. |

`NA`, a reported dash, zero, `Unrated` and `Non-Rated` are intentionally
different states. A dash in the ECL numeric tables is represented as numeric
zero with `value_status: reported_nil` because the table reports a nil amount;
the transfer holdout preserves a dash as `normalized_value: null` where the
source means no reported transaction. This family-specific distinction is
documented in each configuration.

The comparability matrix adds `comparison_id`, left/right record IDs, family,
metric definition, units, scopes, status, reason, permitted conversion,
`override_applied` and an override warning. The six statuses are documented in
`docs/COMPARABILITY.md`.
