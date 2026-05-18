"""Pre-selection engine skeleton.

The implementation is intentionally deferred; this module only declares the
interface expected by downstream signal stages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from contracts.signal_schema import PRE_SELECTION_RESULT_FIELDS

OUTPUT_FILE = "pre_selection_result.csv"
REQUIRED_OUTPUT_FIELDS = PRE_SELECTION_RESULT_FIELDS


class PreSelectionEngine:
    """Interface placeholder for producing pre_selection_result.csv."""

    def run(
        self,
        input_data: Sequence[Mapping[str, Any]] | None = None,
        output_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Return rows that match REQUIRED_OUTPUT_FIELDS."""
        raise NotImplementedError("Pre-selection strategy logic is not implemented yet.")
