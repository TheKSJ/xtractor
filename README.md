# NBFC transfer-of-loans disclosure extraction

This is a small, configuration-driven prototype for extracting one specific
disclosure from three Indian NBFC standalone FY2025-26 annual reports:
transfers of loans through assignment, generally for loans not in default.

It is not a complete annual-report parser. It does not extract full financial
statements, ECL, co-lending data or US-company data. It also does not decide
whether two lenders' figures are financially comparable; that is a separate
review step.

## What is supported

| Lender | Source table | Printed page | PDF page number | Zero-based index | Records |
|---|---|---:|---:|---:|---:|
| Bajaj Finance | Note 58(I)(a) | 358 | 391 | 390 | 24 |
| Cholamandalam Investment and Finance | Note 50, Section III | 232 | 232 | 231 | 14 |
| UGRO Capital | Note 64(a)(i) | 378 | 378 | 377 | 14 |

The page references are deliberately stored in three forms. A printed page is
the number visible on the report page. A PDF page number is one-based. Python
and most PDF libraries use a zero-based index, so PDF page 391 has index 390.

## How the system works

The main engine is [extract_transfer_assignment.py](extract_transfer_assignment.py).
Each lender has a YAML recipe in `config/`. The recipe tells the engine:

1. which local PDF and manifest entry to use;
2. which PDF page to inspect;
3. which table title marks the beginning and which neighbouring title marks the
   end;
4. how the years and value columns are arranged;
5. how many rows/cells are expected;
6. how the exact source row label maps to a canonical metric; and
7. how to normalize numbers, percentages, months, years and text.

The engine reads the PDF text and coordinates, clips the configured table,
groups text into rows and columns, validates the expected shape, and writes
JSON records. It stops when a page, title, boundary, row count, value count,
unit or hash is ambiguous instead of silently guessing.

The old [extract_bajaj_transfer_assignment.py](extract_bajaj_transfer_assignment.py)
file is now a compatibility wrapper. Existing Bajaj commands and imports still
work, but the implementation lives under the lender-neutral name.

## Setup and source files

From the project root in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

Place the unchanged annual-report PDFs at these exact paths:

```text
data/raw/AR_29642_BAJFINANCE_2025_2026_A_23098027_07072026220255.pdf
data/raw/Annual_Report_FY_2025_26_6e549aa38c.pdf
data/raw/1779168341-UGRO Capital Ltd_Annual Report 2025-26.pdf
```

The manifest records the official source link, file name, local path, report
scope and SHA-256 hash:
[config/source_manifest.yaml](config/source_manifest.yaml).

The official source links currently recorded are [Bajaj Finance's FY26 annual
report page](https://www.bajajfinserv.in/finance-digital-annual-report-fy26/index.html),
[Chola's annual-report PDF](https://files.cholamandalam.com/cholafhl/CIFCL_AR_0fa97d6442.pdf),
and [UGRO Capital's investor-relations page](https://www.ugrocapital.com/investor-relation).

The source PDFs are intentionally not committed to Git. Their exact paths are
listed in `.gitignore`; the manifest hash check detects a changed or substituted
local file. Historical download times were not known, so `retrieved_at` is
`null` rather than an invented timestamp.

## Exact commands

Run the lender-neutral engine:

```powershell
.\.venv\Scripts\python.exe .\extract_transfer_assignment.py --config .\config\bajaj_transfer_assignment.yaml
.\.venv\Scripts\python.exe .\extract_transfer_assignment.py --config .\config\chola_transfer_assignment.yaml
.\.venv\Scripts\python.exe .\extract_transfer_assignment.py --config .\config\ugro_transfer_assignment.yaml
```

The old Bajaj command remains valid:

```powershell
.\.venv\Scripts\python.exe .\extract_bajaj_transfer_assignment.py
```

Run the full tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run the independent benchmark and produce both JSON and Markdown reports:

```powershell
.\.venv\Scripts\python.exe .\benchmark_transfer_assignment.py
```

The command returns a non-zero failure status if source checks, record matching
or required comparisons fail. If a lender fails, its fixture records are still
counted in the accuracy denominator and are reported as missing rather than
silently removed.

## Output fields

Each JSON record contains:

- `original_row_label`: the exact row wording found in the report;
- `canonical_metric`: the cross-lender label proposed for analysis;
- `year` and `source_column_position`: the source period and column position;
- `raw_value`: the source cell text, preserved separately;
- `normalized_value`: a machine-friendly number or source text;
- `unit`: for example `INR_crore`, `INR_lakh`, `months`, `years`, `percent`,
  `count` or `text`;
- provenance: company, reporting period, statement scope, source document ID,
  manifest path, source file, PDF page number, PDF index, printed page, note
  and table title.

`NA` is not zero. For Chola, the literal `NA` cells remain records with
`raw_value: "NA"`, `normalized_value: null` and `unit: null`. `Unrated`,
`Non-Rated` and `NA` remain distinct source texts.

## Validation result

The current benchmark covers only these three configured known reports. The
last completed run produced:

| Lender | Expected | Extracted | Correct complete records | Missing | Unexpected | Duplicates |
|---|---:|---:|---:|---:|---:|---:|
| Bajaj | 24 | 24 | 24 | 0 | 0 | 0 |
| Chola | 14 | 14 | 14 | 0 | 0 | 0 |
| UGRO | 14 | 14 | 14 | 0 | 0 | 0 |
| Overall | 52 | 52 | 52 | 0 | 0 | 0 |

Overall complete-record, raw-value, normalized-value, unit, canonical-metric
and provenance-field accuracy were each 100% on this configured benchmark.
The last measured extraction runtime was about 0.20 seconds; it is a runtime
measurement for the code only, not a claim of manual time saved.

The benchmark also rejects values that cross either column edge, rejects partial
numeric matches such as `1,537` inside `1,537.22`, and keeps all expected
records in the denominator when a lender fails.

The machine-readable result is
[outputs/benchmark_transfer_assignment.json](outputs/benchmark_transfer_assignment.json)
and the readable result is
[outputs/benchmark_transfer_assignment_summary.md](outputs/benchmark_transfer_assignment_summary.md).
The pre-edit baseline outputs are under
`outputs/baseline/2026-08-31/`.

## Comparability limits

Extraction correctness and financial comparability are different questions.
The code can correctly preserve a source value while the value is still unsafe
to combine with another lender's value.

- Bajaj's two subcolumns have no visible headings, so they remain
  `column_1`/`column_2`; they are not combined or interpreted.
- Bajaj's weighted average residual maturity remains separate from Chola and
  UGRO weighted average maturity.
- UGRO principal outstanding remains separate from consideration received and
  from generic loan amount assigned.
- UGRO's table excludes co-lending transfers and its security coverage is based
  on secured loans, so scope needs review before comparison.
- Conversions belong in a later analytical layer: lakh ÷ 100 = crore and years
  × 12 = months. The original extracted values are not overwritten.

The detailed metric-by-lender review is in
[docs/comparability_review.md](docs/comparability_review.md). The validation
and risk record is in [docs/error_taxonomy.md](docs/error_taxonomy.md), and
the fixture methodology is in [docs/fixture_review.md](docs/fixture_review.md).
Human visual review of the report pages is still required before financial
sign-off; this project does not write an investment thesis.
