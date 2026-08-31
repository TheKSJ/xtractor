"""Backward-compatible Bajaj entry point for the lender-neutral extractor."""

from extract_transfer_assignment import (
    ExtractionError,
    extract_document,
    load_config,
    load_source_manifest,
    main,
    normalize_value,
    sha256_file,
    validate_page,
    validate_structure,
    write_result,
)

__all__ = [
    "ExtractionError",
    "extract_document",
    "load_config",
    "load_source_manifest",
    "main",
    "normalize_value",
    "sha256_file",
    "validate_page",
    "validate_structure",
    "write_result",
]


if __name__ == "__main__":
    raise SystemExit(main())
