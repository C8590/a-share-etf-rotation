"""Repository-root import bridge for the historical_ml package.

The actual package lives in ``historical_ml/historical_ml`` so the subproject
can also be installed on its own. This bridge keeps ``python -m
historical_ml.cli`` and root-level ``pytest`` working from the repo checkout.
"""

from pathlib import Path

_INNER_PACKAGE = Path(__file__).with_name("historical_ml")
if _INNER_PACKAGE.exists():
    __path__.append(str(_INNER_PACKAGE))
