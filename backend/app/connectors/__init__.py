"""Connectors module: auto-registers all provider implementations."""
from . import (
    gmail,
    google_calendar,
    google_drive,
    google_sheets,
    hubspot,
    slack,
    stripe,
    zoho_books,
)

__all__ = [
    "gmail",
    "google_calendar",
    "google_drive",
    "google_sheets",
    "hubspot",
    "slack",
    "stripe",
    "zoho_books",
]
