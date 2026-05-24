from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

import numpy as np
import pandas as pd

from signal.daily_signal import run_modular_signal_pipeline
from main import SIGNAL_VERSION_V2, _apply_v2_signal_summary
from risk_warning.gate import apply_risk_gate


def _frame(periods: int = 150, start: float = 1.0, drift: float = 0.003) -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-05-18", periods=periods)
    steps = np.arange(periods, dtype=float)
    close = start * np.power(1.0 + drift, steps)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(periods, 2_000_000.0),
            "amount": np.full(periods, 50_000_000.0),
        }
    )


def _pool() -> list[dict[str, str]]:
    return [
        {"symbol": "510300", "name": "沪深300ETF", "sector": "科技"},
        {"symbol": "159915", "name": "创业板ETF", "sector": "科技"},
        {"symbol": "588000", "name": "科创ETF", "sector": "科技"},
        {"symbol": "512480", "name": "半导体ETF", "sector": "科技"},
        {"symbol": "515050", "name": "通信ETF", "sector": "科技"},
        {"symbol": "159995", "name": "芯片ETF", "sector": "科技"},
    ]


class ModularDailySignalPipelineTest(unittest.TestCase):
    def test_pipeline_runs_four_modules_and_writes_daily_summary(self) -> None:
        pool = _pool()
        market_data = {item["symbol"]: _frame(drift=0.002 + index * 0.0002) for index, item in enumerate(pool)}
        holdings = [
            {
                "symbol": "510300",
                "name": "沪深300ETF",
                "shares": 1000,
                "average_buy_price": 1.0,
            }
        ]
        closed_trades = [
            {
                "trade_id": "159915-20260518",
                "symbol": "159915",
                "name": "创业板ETF",
                "buy_date": "2026-05-01",
                "sell_date": "2026-05-18",
                "buy_price": 1.0,
                "sell_price": 1.08,
                "shares": 1000,
                "buy_future_prices": [1.01, 1.02, 1.03],
                "sell_future_prices": [1.08, 1.09, 1.10],
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            result = run_modular_signal_pipeline(
                etf_pool=pool,
                market_data=market_data,
                holdings=holdings,
                closed_trades=closed_trades,
                output_dir=tmp,
                signal_date="2026-05-18",
            )
            output = Path(tmp)

            for filename in (
                "pre_selection_result.csv",
                "entry_signal.csv",
                "exit_signal.csv",
                "learning_report.csv",
                "daily_signal.csv",
                "daily_signal_modular.json",
            ):
                self.assertTrue((output / filename).exists(), filename)

            daily = pd.read_csv(output / "daily_signal.csv")

        self.assertGreater(len(result["pre_selection"]), 0)
        self.assertGreater(len(result["entry"]), 0)
        self.assertEqual(len(result["exit"]), 1)
        self.assertEqual(len(result["learning"]), 1)
        self.assertIn("modular_market_state", daily.columns)
        self.assertIn("modular_buy_actions", daily.columns)
        self.assertIn("modular_exit_actions", daily.columns)
        self.assertIn("modular_learning_advice", daily.columns)
        self.assertNotEqual(str(daily.iloc[0]["modular_candidate_etfs"]), "无")

    def test_pipeline_degrades_gracefully_when_market_data_is_missing(self) -> None:
        pool = [{"symbol": "510300", "name": "沪深300ETF", "sector": "宽基"}]
        holdings = [{"symbol": "510300", "name": "沪深300ETF", "shares": 1000, "average_buy_price": 1.0}]

        with tempfile.TemporaryDirectory() as tmp:
            result = run_modular_signal_pipeline(
                etf_pool=pool,
                market_data={"510300": pd.DataFrame()},
                holdings=holdings,
                closed_trades=[],
                output_dir=tmp,
                signal_date="2026-05-18",
            )
            output = Path(tmp)
            pre_selection = pd.read_csv(output / "pre_selection_result.csv")
            entry = pd.read_csv(output / "entry_signal.csv")
            exit_signal = pd.read_csv(output / "exit_signal.csv")
            learning = pd.read_csv(output / "learning_report.csv")
            daily = pd.read_csv(output / "daily_signal.csv")
            chain = json.loads((output / "daily_signal_modular.json").read_text(encoding="utf-8"))

        self.assertEqual(len(pre_selection), 1)
        self.assertEqual(len(entry), 1)
        self.assertEqual(len(exit_signal), 1)
        self.assertEqual(len(learning), 0)
        for field in ("ml_entry_advice", "ml_confidence", "ml_reason", "ml_action_suggestion"):
            self.assertIn(field, entry.columns)
            self.assertIn(field, chain["entry_signals"][0])
        self.assertIn("modular_pipeline_status", daily.columns)
        self.assertIn("ml_observation_status", daily.columns)
        self.assertEqual(str(entry.iloc[0]["ml_entry_advice"]), "无ML建议")
        self.assertIn(result["summary_fields"]["modular_pipeline_status"], {"已完成", "已完成（含降级）"})

    def test_pipeline_uses_execution_risk_date_when_provided(self) -> None:
        pool = _pool()
        market_data = {item["symbol"]: _frame(drift=0.002 + index * 0.0002) for index, item in enumerate(pool)}
        with tempfile.TemporaryDirectory() as tmp:
            run_modular_signal_pipeline(
                etf_pool=pool,
                market_data=market_data,
                holdings=[],
                closed_trades=[],
                output_dir=tmp,
                signal_date="2026-05-18",
                risk_date="2026-05-19",
            )
            payload = json.loads((Path(tmp) / "risk_gate.json").read_text(encoding="utf-8"))
            learning = pd.read_csv(Path(tmp) / "risk_learning_context.csv")

        self.assertEqual(payload["risk_date"], "2026-05-19")
        self.assertEqual(str(learning.iloc[0]["risk_date"]), "2026-05-19")

    def test_v2_summary_takes_over_final_signal_fields(self) -> None:
        modular_pipeline = {
            "pre_selection": [
                {
                    "trade_date": "2026-05-18",
                    "symbol": "159915",
                    "name": "创业板ETF",
                    "sector": "成长",
                    "score": 88,
                    "rank": 1,
                    "selected": True,
                    "reason": "V2 选前模型入选",
                }
            ],
            "entry": [
                {
                    "trade_date": "2026-05-18",
                    "symbol": "159915",
                    "name": "创业板ETF",
                    "buy_action": "标准买入",
                    "buy_price": "1.234",
                    "position_size": 0.3,
                    "confidence": 0.8,
                    "entry_reason": "V2 买入模型给出标准买入",
                    "ml_entry_advice": "建议等待回踩",
                    "ml_confidence": 0.73,
                    "ml_reason": "历史样本提示当前买点偏急，仅供观察。",
                    "ml_action_suggestion": "WAIT_PULLBACK",
                }
            ],
            "exit": [],
            "summary_fields": {
                "v2_selected_etfs": "159915",
                "v2_market_state": "进攻",
                "v2_selected_sectors": "成长",
                "v2_entry_actions": "159915:标准买入",
                "v2_ml_observation_status": "ML 观察模式已启用（仅供观察，不自动修改交易参数。）",
                "v2_ml_entry_advice": "159915:建议等待回踩（仅供观察，不自动修改交易参数。）",
                "v2_reason": "V2 选前模型入选 | V2 买入模型给出标准买入",
                "fallback_reason": "无",
            },
        }
        summary = {
            "target_symbols": "510300",
            "suggested_buy": "510300",
            "buy_plan": "[]",
            "rank_table": "[]",
        }

        _apply_v2_signal_summary(summary, modular_pipeline)

        self.assertEqual(summary["signal_version"], SIGNAL_VERSION_V2)
        self.assertEqual(summary["target_symbols"], "159915")
        self.assertEqual(summary["suggested_buy"], "159915")
        self.assertIn("V2 买入模型", summary["buy_plan"])
        self.assertIn("建议等待回踩", summary["buy_plan"])
        self.assertIn("仅供观察，不自动修改交易参数", summary["buy_plan"])
        self.assertIn("V2 选前模型", summary["rank_table"])
        self.assertIn("ml_entry_advice", summary["rank_table"])
        self.assertEqual(summary["ml_observation_status"], "ML 观察模式已启用（仅供观察，不自动修改交易参数。）")
        self.assertNotIn("v1_selected_etfs", summary)

    def test_ml_observation_does_not_bypass_risk_gate(self) -> None:
        row = {
            "symbol": "159915",
            "buy_action": "标准买入",
            "position_size": 0.5,
            "ml_entry_advice": "建议升级小仓试探",
            "ml_confidence": 0.81,
            "ml_reason": "ML 仅观察，不应覆盖风控。",
            "ml_action_suggestion": "UPGRADE_PROBE",
        }

        gated = apply_risk_gate(
            [row],
            {
                "risk_level": "R4",
                "risk_score": 99,
                "freeze_entry": True,
                "equity_cap_override": 0.0,
                "require_manual_review": True,
                "manual_takeover_required": True,
                "affected_sectors": [],
                "explain": "测试风控冻结",
            },
        )[0]

        self.assertNotEqual(gated["buy_action"], row["buy_action"])
        self.assertEqual(gated["position_size"], 0.0)
        self.assertEqual(gated["ml_entry_advice"], row["ml_entry_advice"])
        self.assertEqual(gated["ml_action_suggestion"], row["ml_action_suggestion"])


if __name__ == "__main__":
    unittest.main()
