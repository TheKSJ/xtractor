# Indian Lender Disclosure Intelligence

This project turns difficult notes-level disclosures in Indian lender annual reports into source-linked, validated and comparison-aware data for analysts, reviewers and developers.

It answers what changed, which figures are comparable, which must be excluded, what warnings affect interpretation, and exactly where each value came from.

It does not predict stock prices, build a trading strategy, produce a composite risk score, or replace human review of annual-report pages.

Notes-level disclosures are difficult because annual reports mix printed and PDF page numbers, continuation tables, merged cells, lender-specific populations, ambiguous columns, footnotes and literal missing-value labels. This system is for credit analysts, equity analysts, audit/review teams and developers who need those distinctions to remain visible.

## Supported disclosures

| Disclosure | Lenders | Known-report records |
|---|---|---:|
| Transfer through assignment | Bajaj Finance, Cholamandalam Investment and Finance, UGRO Capital | 52 |
| ECL allowance/stage movement | Bajaj Finance, Cholamandalam Investment and Finance, UGRO Capital | 240 |
| Holdout transfer table | Mahindra & Mahindra Financial Services | 24 |

The known transfer benchmark is 52/52 complete records: Bajaj 24/24, Chola 14/14 and UGRO 14/14. The ECL benchmark is 240/240 structurally extracted records with 26/26 reconciliation groups passing. These are configured-report results, not unseen-report accuracy.

| Benchmark case | Expected | Extracted | Reconciliations | Result |
|---|---:|---:|---:|---|
| Bajaj ECL loans | 80 | 80 | 8/8 pass | passed |
| Chola ECL term loans | 80 | 80 | 8/8 pass | passed |
| UGRO ECL advances | 80 | 80 | 10/10 pass | passed |
| Mahindra holdout transfer table | 24 | 24 | n/a | corrected pass |

## Quick start

```powershell
python -m pip install -e .
lender-intel demo --output demo-output
lender-intel extract --config config\bajaj_transfer_assignment.yaml
lender-intel benchmark --suite all
lender-intel analyze --input outputs --output analyst-output
```

Real annual-report PDFs are ignored by Git. Put them under `data/raw/` and verify SHA-256 values in `config/source_manifest.yaml`. The synthetic demo and CI do not require those PDFs.

The demo writes `records.json`, `records.csv`, `comparability_matrix.json`, `validation_report.json`, `warnings.json`, `analyst_brief.md` and `analyst_brief.html`. To reproduce the documented holdout workflow locally, run `lender-intel holdout --config config\mahindra_holdout_transfer_assignment.yaml --output holdout-output` after downloading the manifest PDF.

## Architecture

1. Configuration-driven PDF extraction preserves raw text, coordinates, units, labels and provenance.
2. Validation checks table boundaries, complete numbers, source hashes, row/cell counts, duplicates and missing records.
3. Semantic mappings keep definitions, populations, periods, exclusions and stages explicit.
4. Comparability decisions block unsafe calculations by default and record any override rationale.
5. Analysis performs only permitted conversions, changes and reconciliations.
6. Reporting writes JSON, CSV, Markdown and self-contained HTML outputs.

Example record-level output (abbreviated):

```json
{"company":"Bajaj Finance Limited","canonical_stage":"Stage 3","canonical_movement_category":"closing_balance","year":2026,"normalized_value":2859.31,"unit":"INR_crore","pdf_page_number":295}
```

## Comparability safeguards

Unit conversion is not proof of economic comparability. Bajaj's unidentified assignment subcolumns remain separate. UGRO principal outstanding and consideration are not merged with generic loan amount assigned. Maturity and residual maturity remain distinct. `NA`, zero, `Unrated`, `Non-Rated` and reported nil remain distinct. Cross-lender scope mismatches are blocked until explicitly reviewed.

## Repository layout

`lender_intel/` contains the reusable package. `config/` contains lender recipes, the source manifest and the comparability registry. `tests/` contains regression, synthetic and negative tests. `outputs/` contains reproducible example results. `docs/` contains methodology and learning material.

## Testing and reproduction

```powershell
python -m unittest discover -s tests -v
python benchmark_transfer_assignment.py
python benchmark_ecl.py
lender-intel demo --output demo-output
```

See [REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md) for real-report setup and holdout reproduction. See [LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md) before presenting the project to a finance or technical interviewer.

Financial sign-off still requires visual review of every cited page. A source hash, row count or passing reconciliation demonstrates a reproducible extraction check; it does not establish a lender's financial correctness or an investment conclusion.

## Limitations and roadmap

The system is configuration-driven and currently covers a small set of annual-report layouts. Scanned PDFs, changed layouts, ambiguous columns and new lenders require review and new fixtures. It does not extract every financial statement or independently validate AUM denominators. Future work may add more lenders, OCR review workflows and richer source-audit tooling without weakening comparability gates.
