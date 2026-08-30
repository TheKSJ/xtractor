# Bajaj transfer-assignment extractor

This project extracts only Note 58(I)(a), "Details of transfer through assignment in respect of loans not in default", from the configured Bajaj Finance annual-report page.

Run from the project root:

```powershell
.\.venv\Scripts\python.exe .\extract_bajaj_transfer_assignment.py
```

Run the focused tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The extractor writes `outputs/bajaj_transfer_assignment.json`. Always compare its values visually with printed annual-report page 358 before relying on them.
