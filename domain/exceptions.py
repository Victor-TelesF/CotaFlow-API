"""Errors raised by domain validation and financial rules."""


class DomainError(Exception):
    """Base error for CotaFlow financial rules."""


class InvalidValueError(DomainError):
    """An input does not satisfy its domain contract."""


class InvalidPriceError(InvalidValueError):
    """A unit price is invalid."""


class InvalidQuantityError(InvalidValueError):
    """A transaction quantity is invalid."""


class InvalidTransactionTypeError(InvalidValueError):
    """The operation is neither a buy nor a sell."""


class InvalidTransactionDateError(InvalidValueError):
    """The operation date is invalid or in the future."""


class AssetNotFoundError(DomainError):
    """The requested ticker has no open position."""


class InsufficientBalanceError(DomainError):
    """A sale would produce a negative historical balance."""


class MissingPriceError(DomainError):
    """A required price is missing from the supplied snapshot."""
