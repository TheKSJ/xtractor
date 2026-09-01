# Methodology

The extractor reads configured annual-report pages with PyMuPDF. Configuration supplies source identity, page/index pairs, table boundaries, column edges, row vocabulary, units and populations. Raw source strings and positions are retained; normalized values are separate.

Validation is fail-closed. Missing headings, periods, units, rows, values, source hashes or boundaries stop a strict run. Benchmark denominators include expected records even when extraction fails. ECL extraction covers Bajaj loans, Chola term loans and UGRO advances. Totals and purchased/originated credit-impaired columns remain unresolved stages.

Reconciliation is `opening + additive movements - closing`. Transfer subtotals are preserved but excluded from that sum. Analysis uses Decimal arithmetic, explicit unit conversions and cautious interpretations; human visual review remains required.
