"""Interface contracts shared by signal modules."""

from .signal_schema import (
    ENTRY_SIGNAL_FIELDS,
    EXIT_SIGNAL_FIELDS,
    LEARNING_REPORT_FIELDS,
    PRE_SELECTION_RESULT_FIELDS,
    BuyAction,
    FailureAttribution,
    FieldName,
    MarketState,
    SellAction,
)

__all__ = [
    "BuyAction",
    "ENTRY_SIGNAL_FIELDS",
    "EXIT_SIGNAL_FIELDS",
    "FailureAttribution",
    "FieldName",
    "LEARNING_REPORT_FIELDS",
    "MarketState",
    "PRE_SELECTION_RESULT_FIELDS",
    "SellAction",
]
