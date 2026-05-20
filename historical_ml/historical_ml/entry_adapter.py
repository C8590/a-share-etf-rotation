from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
import importlib.util
import sys

import numpy as np
import pandas as pd

from .config import HistoricalMLConfig
from .schemas import ENTRY_CANDIDATE_COLUMNS
from .io_utils import reorder_columns


class EntryAdapter(Protocol):
    """Adapter interface for replaying entry decisions.

    historical_ml must not edit entry rules.  Production use should implement
    this protocol by calling the entry department's historical decision logic as
    a black box and returning candidate/selected/bought flags.
    """

    def build_entry_candidates(
        self,
        etf_samples: pd.DataFrame,
        sector_samples: pd.DataFrame,
        signal_date,
        execution_date,
        config: HistoricalMLConfig,
    ) -> pd.DataFrame:
        ...


@dataclass
class RealEntryAdapter:
    """Call the existing entry engine without changing its trading rules.

    The adapter translates replay features into the pre-selection row contract
    consumed by ``signal/entry/engine.py``. Future labels are deliberately not
    part of this path; they are only attached later by ``FutureLabeler``.
    """

    project_root: str | Path | None = None
    _entry_engine_cls: Any = field(default=None, init=False, repr=False)
    _buy_actions: set[str] | None = field(default=None, init=False, repr=False)

    def build_entry_candidates(
        self,
        etf_samples: pd.DataFrame,
        sector_samples: pd.DataFrame,
        signal_date,
        execution_date,
        config: HistoricalMLConfig,
    ) -> pd.DataFrame:
        if etf_samples.empty:
            return pd.DataFrame(columns=ENTRY_CANDIDATE_COLUMNS)

        entry_engine_cls, buy_actions = self._entry_engine()
        df = etf_samples.copy()
        signal_date = pd.Timestamp(signal_date).normalize()
        execution_date = pd.NaT if pd.isna(execution_date) else pd.Timestamp(execution_date).normalize()
        pre_selection_rows = self._to_pre_selection_rows(df, sector_samples, signal_date, config)

        entry_engine = entry_engine_cls(generated_at=signal_date.isoformat())
        entry_rows = [
            entry_engine._build_output_row(row, signal_date.isoformat())
            for row in pre_selection_rows
        ]

        entry_by_code = {self._symbol(row.get("symbol")): row for row in entry_rows}
        pre_by_code = {self._symbol(row.get("symbol")): row for row in pre_selection_rows}

        was_candidate: list[bool] = []
        was_selected: list[bool] = []
        was_bought: list[bool] = []
        reasons: list[str] = []

        for _, row in df.iterrows():
            code = self._symbol(row.get("code"))
            pre_row = pre_by_code.get(code, {})
            entry_row = entry_by_code.get(code, {})
            selected = self._truthy(pre_row.get("selected"))
            buy_action = str(entry_row.get("buy_action", ""))
            position_size = self._number(entry_row.get("position_size"))
            bought = buy_action in buy_actions and position_size > 0

            was_candidate.append(bool(pre_row))
            was_selected.append(selected)
            was_bought.append(bought)
            reasons.append(self._reason(pre_row, entry_row, selected, bought))

        out = df.assign(
            signal_date=signal_date,
            execution_date=execution_date,
            was_candidate=was_candidate,
            was_selected=was_selected,
            was_bought=was_bought,
            exclude_reason=reasons,
            source=config.source,
        )
        return reorder_columns(out, ENTRY_CANDIDATE_COLUMNS)

    def _to_pre_selection_rows(
        self,
        etf_samples: pd.DataFrame,
        sector_samples: pd.DataFrame,
        signal_date: pd.Timestamp,
        config: HistoricalMLConfig,
    ) -> list[dict[str, Any]]:
        selected_codes = set(self._selected_codes(etf_samples, sector_samples, config))
        rows: list[dict[str, Any]] = []
        for _, row in etf_samples.sort_values(["global_rank", "sector_rank", "etf_rank"]).iterrows():
            code = self._symbol(row.get("code"))
            selected = code in selected_codes
            rows.append(
                {
                    "trade_date": signal_date.date().isoformat(),
                    "symbol": code,
                    "name": str(row.get("name", code)),
                    "sector": str(row.get("sector", "")),
                    "market_state": self._entry_market_state(row.get("market_state")),
                    "score": self._entry_score(row.get("entry_score")),
                    "rank": self._rank(row.get("global_rank")),
                    "selected": selected,
                    "reason": self._pre_selection_reason(row, selected, config),
                    "generated_at": signal_date.isoformat(),
                    "close": row.get("close"),
                    "momentum_20": row.get("r20"),
                    "momentum_60": row.get("r60"),
                    "momentum_120": row.get("r120"),
                    "distance_ma20": self._distance(row.get("close"), row.get("ma20")),
                    "distance_ma60": self._distance(row.get("close"), row.get("ma60")),
                }
            )
        return rows

    def _selected_codes(
        self,
        etf_samples: pd.DataFrame,
        sector_samples: pd.DataFrame,
        config: HistoricalMLConfig,
    ) -> list[str]:
        selected_sectors = set(
            sector_samples.loc[
                sector_samples["sector_rank"] <= config.selected_sector_count,
                "sector",
            ].astype(str)
        )
        selected: list[str] = []
        selected_by_sector: set[str] = set()

        for _, row in etf_samples.sort_values(["global_rank", "sector_rank", "etf_rank"]).iterrows():
            code = self._symbol(row.get("code"))
            sector = str(row.get("sector", ""))
            if len(selected) >= config.max_selected_entries:
                break
            if sector in selected_by_sector:
                continue
            if sector not in selected_sectors:
                continue
            if str(row.get("data_quality_flag", "ok")) != "ok":
                continue
            if self._number(row.get("entry_score")) < config.min_entry_score:
                continue
            if self._rank(row.get("etf_rank")) > config.candidate_top_n_per_sector:
                continue
            selected.append(code)
            selected_by_sector.add(sector)
        return selected

    def _pre_selection_reason(self, row: pd.Series, selected: bool, config: HistoricalMLConfig) -> str:
        if str(row.get("data_quality_flag", "ok")) != "ok":
            return f"filtered:data_abnormal:{row.get('data_quality_flag')}"
        if self._rank(row.get("etf_rank")) > config.candidate_top_n_per_sector:
            return "filtered:etf_rank_below_candidate_cutoff"
        if self._number(row.get("entry_score")) < config.min_entry_score:
            return "filtered:entry_score_below_threshold"
        if selected:
            return "selected_by_historical_replay"
        return "filtered:not_selected_by_historical_replay"

    def _reason(self, pre_row: dict[str, Any], entry_row: dict[str, Any], selected: bool, bought: bool) -> str:
        parts = [str(pre_row.get("reason", "")).strip()]
        buy_action = str(entry_row.get("buy_action", "")).strip()
        if buy_action:
            parts.append(f"entry_action:{buy_action}")
        if not selected:
            parts.append("entry_not_selected")
        elif not bought:
            parts.append("entry_not_bought")
        else:
            parts.append("entry_bought")
        return "|".join(part for part in parts if part)

    def _load_entry_engine(self):
        root = self._project_root()
        module_path = root / "signal" / "entry" / "engine.py"
        if not module_path.exists():
            raise ImportError(f"entry engine not found: {module_path}")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        module_name = "_historical_ml_real_entry_engine"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load entry engine from {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        buy_actions = {
            str(getattr(module.BuyAction, "PROBE_BUY").value),
            str(getattr(module.BuyAction, "STANDARD_BUY").value),
            str(getattr(module.BuyAction, "ADD_BUY").value),
        }
        return module.EntryEngine, buy_actions

    def _entry_engine(self):
        if self._entry_engine_cls is None or self._buy_actions is None:
            self._entry_engine_cls, self._buy_actions = self._load_entry_engine()
        return self._entry_engine_cls, self._buy_actions

    def _project_root(self) -> Path:
        if self.project_root is not None:
            return Path(self.project_root).resolve()
        return Path(__file__).resolve().parents[2]

    @staticmethod
    def _entry_market_state(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"offense", "attack"}:
            return "attack"
        if text in {"neutral", "balanced", "balance"}:
            return "balanced"
        if text in {"defense", "defensive"}:
            return "defense"
        return str(value or "balanced")

    @staticmethod
    def _entry_score(value: Any) -> float:
        number = RealEntryAdapter._number(value)
        return number * 100 if abs(number) <= 5 else number

    @staticmethod
    def _distance(close: Any, moving_average: Any) -> float:
        close_n = RealEntryAdapter._number(close)
        ma_n = RealEntryAdapter._number(moving_average)
        if close_n <= 0 or ma_n <= 0:
            return 0.0
        return close_n / ma_n - 1.0

    @staticmethod
    def _number(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return 0.0 if pd.isna(number) else number

    @staticmethod
    def _rank(value: Any) -> int:
        number = RealEntryAdapter._number(value)
        return int(number) if number > 0 else 10**9

    @staticmethod
    def _symbol(value: Any) -> str:
        text = str(value or "").strip()
        return text.zfill(6) if text.isdigit() else text

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "selected"}


@dataclass
class HeuristicEntryAdapter:
    """Dependency-light fallback adapter for bootstrapping sample production.

    This is not a trading rule.  It only creates analyzable historical samples
    until the real entry adapter is wired in.
    """

    include_all_etfs_as_samples: bool = True

    def build_entry_candidates(
        self,
        etf_samples: pd.DataFrame,
        sector_samples: pd.DataFrame,
        signal_date,
        execution_date,
        config: HistoricalMLConfig,
    ) -> pd.DataFrame:
        if etf_samples.empty:
            return pd.DataFrame(columns=ENTRY_CANDIDATE_COLUMNS)

        df = etf_samples.copy()
        signal_date = pd.Timestamp(signal_date).normalize()
        execution_date = pd.NaT if pd.isna(execution_date) else pd.Timestamp(execution_date).normalize()
        selected_sectors = set(
            sector_samples.loc[sector_samples["sector_rank"] <= config.selected_sector_count, "sector"].astype(str)
        )

        reasons: list[str] = []
        was_candidate = []
        was_selected = []
        was_bought = []
        selected_by_sector: set[str] = set()
        selected_count = 0

        ranked = df.sort_values(["global_rank", "sector_rank", "etf_rank"]).copy()
        decision_map: dict[str, tuple[bool, bool, bool, str]] = {}

        for _, row in ranked.iterrows():
            reason_parts = []
            code = str(row["code"])
            sector = str(row["sector"])
            market_state = str(row.get("market_state", "unknown"))
            data_quality_flag = str(row.get("data_quality_flag", "ok"))
            entry_score = row.get("entry_score", np.nan)
            sector_rank = row.get("sector_rank", np.nan)
            etf_rank = row.get("etf_rank", np.nan)

            candidate = True
            if data_quality_flag != "ok":
                candidate = False
                reason_parts.append(f"data_abnormal:{data_quality_flag}")
            if sector not in selected_sectors:
                candidate = False
                reason_parts.append("sector_not_selected")
            if pd.notna(etf_rank) and int(etf_rank) > config.candidate_top_n_per_sector:
                candidate = False
                reason_parts.append("etf_rank_below_candidate_cutoff")
            if pd.notna(entry_score) and entry_score < config.min_entry_score:
                candidate = False
                reason_parts.append("entry_score_below_threshold")
            if market_state == "defense" and not config.defense_allows_candidate:
                candidate = False
                reason_parts.append("market_defense_block_candidate")

            selected = False
            bought = False
            if candidate:
                if sector in selected_by_sector:
                    reason_parts.append("same_sector_skipped")
                elif selected_count >= config.max_selected_entries:
                    reason_parts.append("portfolio_slot_limit")
                else:
                    selected = True
                    selected_by_sector.add(sector)
                    selected_count += 1

            if selected:
                bought = True
                if market_state == "defense" and not config.defense_allows_bought:
                    bought = False
                    reason_parts.append("market_defense_block_bought")
                if pd.isna(execution_date):
                    bought = False
                    reason_parts.append("no_execution_date")

            if not reason_parts:
                reason_parts.append("selected" if selected else "not_selected")
            decision_map[code] = (candidate, selected, bought, "|".join(reason_parts))

        for _, row in df.iterrows():
            candidate, selected, bought, reason = decision_map[str(row["code"])]
            was_candidate.append(candidate)
            was_selected.append(selected)
            was_bought.append(bought)
            reasons.append(reason)

        out = df.assign(
            signal_date=signal_date,
            execution_date=execution_date,
            was_candidate=was_candidate,
            was_selected=was_selected,
            was_bought=was_bought,
            exclude_reason=reasons,
            source=config.source,
        )

        # Keep all rows by default so missed winners / filtered top ranks can be studied.
        if not self.include_all_etfs_as_samples:
            out = out.loc[out["was_candidate"] | (out["global_rank"] <= config.candidate_top_n_per_sector * 2)].copy()

        return reorder_columns(out, ENTRY_CANDIDATE_COLUMNS)
