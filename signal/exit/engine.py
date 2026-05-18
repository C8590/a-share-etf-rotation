"""Exit engine skeleton.

The implementation is intentionally deferred; this module only declares the
interface expected by the trading workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from contracts.signal_schema import EXIT_SIGNAL_FIELDS

OUTPUT_FILE = "exit_signal.csv"
REQUIRED_OUTPUT_FIELDS = EXIT_SIGNAL_FIELDS


class ExitEngine:
    """Interface placeholder for producing exit_signal.csv."""

    def run(
        self,
        holdings: Sequence[Mapping[str, Any]] | None = None,
        output_dir: str | Path | None = None,
    ) -> list[dict[str, Any]]:
        """Return rows that match REQUIRED_OUTPUT_FIELDS."""
        raise NotImplementedError("Exit strategy logic is not implemented yet.")
