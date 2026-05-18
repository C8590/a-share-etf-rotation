"""Entry engine skeleton.

The implementation is intentionally deferred; this module only declares the
interface expected by the trading workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from contracts.signal_schema import ENTRY_SIGNAL_FIELDS

OUTPUT_FILE = "entry_signal.csv"
REQUIRED_OUTPUT_FIELDS = ENTRY_SIGNAL_FIELDS


class EntryEngine:
    """Interface placeholder for producing entry_signal.csv."""

    def run(
        self,
        pre_selection_rows: Sequence[Mapping[str, Any]] | None = None,
        output_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Return rows that match REQUIRED_OUTPUT_FIELDS."""
        raise NotImplementedError("Entry strategy logic is not implemented yet.")
