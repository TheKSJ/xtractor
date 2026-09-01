# Reproducibility

## PDF-free verification

From the repository root:

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
lender-intel demo --output demo-output
lender-intel analyze --input demo-output --output analyst-output
```

The demo is deterministic and uses no annual-report PDF. GitHub Actions runs the same test and demo path on Python 3.10. Generated demo and analyst directories are ignored by Git.

## Known-report extraction

Real PDFs are intentionally excluded from the repository. Download the official files listed in `config/source_manifest.yaml` into `data/raw/`, then run:

```powershell
lender-intel extract --config config\bajaj_transfer_assignment.yaml
lender-intel extract --config config\chola_transfer_assignment.yaml
lender-intel extract --config config\ugro_transfer_assignment.yaml
lender-intel extract --config config\bajaj_ecl_stage_movement.yaml
lender-intel extract --config config\chola_ecl_stage_movement.yaml
lender-intel extract --config config\ugro_ecl_stage_movement.yaml
python benchmark_transfer_assignment.py
python benchmark_ecl.py
lender-intel analyze --input outputs --output analyst-output
```

Every configured source is checked against its SHA-256 and repository path. The transfer benchmark keeps all 52 expected records in its denominator (Bajaj 24, Chola 14, UGRO 14). The ECL benchmark expects 240 records (80 per lender), runs 26 arithmetic reconciliation groups and checks three independently transcribed spot values.

## Holdout reproduction

The Mahindra report was obtained from the official URL in the manifest. Its SHA-256, retrieval date, period and standalone scope are recorded. After downloading it to the configured `data/raw/` path, run:

```powershell
lender-intel holdout --config config\mahindra_holdout_transfer_assignment.yaml --output holdout-output
```

The command publishes `holdout-output/evaluation.json`, `holdout-output/final_extraction.json` and `outputs/mahindra_holdout_transfer_assignment.json`. The initial generic single-region attempt failed because the source uses two visual regions on one PDF page; the lender-specific configuration then extracted 24/24 records. This is a correction/generalization evaluation, not a blind or unseen-report accuracy claim, because the generic failure and correction were observed during development.

## Source and environment audit

Use `config/source_manifest.template.yaml` for a new report. Record an official URL, exact local filename, repository path, retrieval date and lowercase SHA-256. Do not commit copyrighted PDFs. Keep the installed package and dependency versions visible in the environment when publishing a run.

If a page, heading, unit, row, value or source hash changes, the strict extractor stops. Update the recipe only after visually reviewing the new page and adding a fixture/test; do not widen boundaries until the intended table is unambiguous.
