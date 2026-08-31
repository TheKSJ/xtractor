# Error taxonomy and validation record

This document separates problems that really happened during this project from
negative tests that were intentionally created, and from risks that have not
yet occurred. The distinction matters: a passing test is evidence about the
tested case, not proof that every future annual report will work.

## Observed during this project

| Category | What was observed | How the system detects it | Impact | Response |
|---|---|---|---|---|
| Wrong configuration shape | An earlier Bajaj configuration did not contain the nested `document.company` field expected by the loader. | `load_config()` checks required paths before extraction. | Extraction stopped before reading financial values. | Configuration was repaired; the loader remains strict. |
| Wrong or stale source path | A source-file reference did not match the PDF available in `data/raw`. | The configured path must exist, and the manifest repository path must match it. | The run cannot be reproduced from the declared input. | The path and manifest were aligned to the local report. |
| Font/text-layer corruption | Bajaj's PDF text layer emits `C` for the rupee glyph in the `ITFRupee` font. | The extractor applies a narrowly configured font repair and records a warning. | Raw text can otherwise be misread even when the visible PDF is correct. | The printed rupee representation is restored in `raw_value`; the warning stays visible. |
| Adjacent-table/layout differences | Chola has continuation and neighbouring sections around Note 50; UGRO splits long labels and has a following `ii)` section. | Target-title uniqueness, explicit boundaries, page coordinates and expected row/cell counts are checked. | A broad text scrape could include the wrong disclosure. | Each lender has layout-specific configuration and boundary tests. |
| Literal missing/reporting text | Chola prints `NA` for security coverage and rating distribution. | `NA` is preserved as `raw_value: "NA"` and normalized to `null`, with no invented zero or rating record. | Treating missing as zero would change the financial meaning. | The source cells are retained as records, but missingness remains explicit. |
| Semantic maturity difference | Bajaj says weighted average residual maturity; Chola says weighted average maturity; UGRO says maturity in years. | Separate canonical names and warnings prevent automatic equivalence. | Combining them could compare different populations or definitions. | The comparability review marks the relationship unresolved. |

These were observed extraction/configuration issues, not evidence of financial
misstatement by any lender.

## Deliberately constructed negative tests

The test suite intentionally creates invalid inputs so the system demonstrates
that it fails safely:

| Test | Expected response |
|---|---|
| Page number and zero-based PDF index disagree | Raise `ExtractionError`; do not read a guessed page. |
| Configured page is outside the PDF | Raise `ExtractionError`. |
| Target title is missing | Raise `ExtractionError`. |
| A record is removed before structural validation | Raise `ExtractionError` because row/cell counts no longer match. |
| Local PDF hash is changed in the manifest | Raise `ExtractionError` for SHA-256 mismatch. |
| Non-numeric or fractional loan count is supplied | Raise `ExtractionError`; a count must be an integer. |
| Currency text is malformed | Raise `ExtractionError`; the unit parser does not guess. |
| Benchmark receives missing, duplicate or unexpected records | Report them separately and retain missing records in the denominator. |

These are tests of defensive behaviour. They are not historical extraction
failures.

## Risks not yet observed

The current three-report benchmark does not test every possible future PDF.
Known unobserved risks include:

- a future report using a scanned image with no usable text layer;
- a changed table layout, reordered columns, merged cells or a new heading;
- an incorrect official URL or a replacement PDF whose hash no longer matches;
- a lender reporting the same words with a different population or footnote;
- a new rating label, unit, scale or missing-value convention;
- an ambiguous table title appearing more than once on a page;
- a source requiring OCR or a manual review step;
- an unseen lender, disclosure type or reporting year.

The safe response to these cases is to stop or flag the extraction for review,
update the configuration and fixture with source evidence, then add a focused
test. The extractor should not silently infer a value, unit, row, column or
cross-lender equivalence.
