class LenderIntelError(RuntimeError):
    """Base error for strict validation and reporting failures."""


class ConfigurationError(LenderIntelError):
    pass


class ExtractionError(LenderIntelError):
    pass


class ComparabilityError(LenderIntelError):
    pass
