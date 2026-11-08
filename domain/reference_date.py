"""Provide a reference date for validation and comparisons."""

from datetime import date


def reference_date() -> date:
    """Return the current reference date.

    Returns:
        date: The current date.
    """
    return date.today()
