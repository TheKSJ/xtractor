# Reference-fixture review

Reference fixtures are expected values used to test the extractor. They are
not created by running the extractor and copying its output.

## What is checked independently

`benchmark_transfer_assignment.py` opens each local PDF directly, without
calling `extract_document()`, and checks:

- source file existence, source identity and page number/index agreement;
- configured printed page reference;
- target title and neighbouring section boundaries;
- configured years;
- every fixture row label;
- every fixture raw value's presence in the source page;
- complete-number matching, so `1,537` cannot match inside `1,537.22`;
- the expected source unit and text representation;
- both left and right boundaries of each expected value column, plus the row
  band containing the value;
- fixture provenance, using the embedded provenance object for Chola/UGRO and
  the Bajaj provenance sidecar.

The benchmark then runs the extractor separately and compares its output to
the expected fixture records using:

`source_document_id + original_row_label + year + source_column_position`

Normalized numbers are compared numerically. `raw_value` is compared as text,
so a visually equivalent but differently represented source string remains
visible for review. Missing expected records remain in every accuracy
denominator, including when extraction fails. Duplicate and unexpected records
are reported separately.

## Human review status

The source audit is automated and reproducible, but its report deliberately
sets `human_verified` to `false`. A human must visually inspect the rendered
annual-report page before financial sign-off. The local evidence images for
the Chola review are:

- [Chola Note 50 context page](../outputs/chola_note_50_context_page_231.png)
- [Chola Note 50 Section III page](../outputs/chola_note_50_iii_page_232.png)

The PDFs are kept locally for reproducibility and are excluded from Git by
their exact paths in `.gitignore`. Their SHA-256 values are recorded in
[config/source_manifest.yaml](../config/source_manifest.yaml).
