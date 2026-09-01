# Architecture

Root extraction scripts remain compatibility entry points for the verified baseline. `lender_intel.ecl` adds the ECL parser, `comparability` produces semantic decisions, `analysis` calculates changes and reconciliations, `reporting` writes deterministic bundles, and `cli` exposes commands.

Data flows from source PDF to raw records, validation, semantic registry, permitted calculations and reports. Raw extraction is never overwritten by normalization. The package uses dataclasses, Decimal and local files; it does not require a database, dashboard or predictive model.
