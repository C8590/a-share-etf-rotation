"""Learning engine skeleton.

The implementation is intentionally deferred; this module only declares the
interface expected by review and feedback workflows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from contracts.signal_schema import LEARNING_REPORT_FIELDS

OUTPUT_FILE = "learning_report.csv"
REQUIRED_OUTPUT_FIELDS = LEARNING_REPORT_FIELDS


class LearningEngine:
    """Interface placeholder for producing learning_report.csv."""

    def run(
        self,
        closed_trades: Sequence[Mapping[str, Any]] | None = None,
        output_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Return rows that match REQUIRED_OUTPUT_FIELDS."""
        raise NotImplementedError("Learning report logic is not implemented yet.")
