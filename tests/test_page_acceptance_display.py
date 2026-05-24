from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from app import (
    REQUIRED_ACCEPTANCE_OUTPUT_FILES,
    _actual_buy_plan_frame,
    _candidate_action_text,
    _clean_display_frame,
    _v21_display_value,
    _v21_frame,
    _load_control_output,
    build_v2_etf_lookup,
    build_v2_action_table,
    build_v2_candidate_table,
    build_hindsight_sample_status,
    build_output_file_status,
)
from data.portfolio_store import load_portfolio, save_portfolio
from ui.components import localize_columns
from ui.signal_parser import load_dashboard_data
from ui.signal_parser import parse_rank_table


class PageAcceptanceDisplayTest(unittest.TestCase):
    def test_output_file_status_degrades_when_outputs_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            (output_dir / "daily_decision_snapshot.json").write_text("{}\n", encoding="utf-8")
            status = build_output_file_status(output_dir)

        self.assertEqual(set(status["输出文件"]), set(REQUIRED_ACCEPTANCE_OUTPUT_FILES))
        missing = status[status["输出文件"] == "order_intent.json"].iloc[0]
        present = status[status["输出文件"] == "daily_decision_snapshot.json"].iloc[0]
        self.assertIn("缺失", missing["读取状态"])
        self.assertEqual(present["读取状态"], "已生成")

    def test_candidate_action_text_explains_observation_and_price_validation(self) -> None:
        self.assertEqual(_candidate_action_text("观察"), "候选观察（不是买入）")
        self.assertEqual(_candidate_action_text("159915:观察"), "159915:候选观察（不是买入）")
        self.assertIn("不等于买入信号", _candidate_action_text("校验通过"))

    def test_actual_buy_plan_filters_observation_rows(self) -> None:
        frame = pd.DataFrame(
            [
                {"ETF代码": "159915", "交易动作": "观察", "建议仓位": "0"},
                {"ETF代码": "510300", "交易动作": "标准买入", "建议仓位": "0.6"},
            ]
        )

        actual = _actual_buy_plan_frame(frame)

        self.assertEqual(actual["ETF代码"].tolist(), ["510300"])

    def test_selected_label_is_candidate_pool_not_buy(self) -> None:
        localized = localize_columns(pd.DataFrame([{"selected": True, "final_signal": "selected"}]))

        self.assertIn("是否进入候选池", localized.columns)
        self.assertEqual(localized.iloc[0]["是否进入候选池"], "是")
        self.assertEqual(localized.iloc[0]["最终信号"], "进入候选池")

    def test_rank_table_uses_candidate_pool_language(self) -> None:
        row = pd.Series(
            {
                "rank_table": '[{"symbol":"159915","name":"创业板ETF","selected":true,"final_signal":"selected","selection_reason":"入选"}]'
            }
        )

        table = parse_rank_table(row)

        self.assertIn("是否进入候选池", table.columns)
        self.assertIn("候选 / 过滤原因", table.columns)
        self.assertEqual(table.iloc[0]["最终信号"], "进入候选池")

    def test_v2_control_output_missing_file_degrades_to_empty_frame(self) -> None:
        frame = _load_control_output("__missing_acceptance_file__.csv")

        self.assertTrue(frame.empty)

    def test_internal_status_values_are_not_exposed_directly(self) -> None:
        cleaned = _clean_display_frame(
            pd.DataFrame([{"状态": "up_to_date", "候选状态": "selected", "空值": None, "缺失值": float("nan")}])
        )
        text = " ".join(cleaned.iloc[0].astype(str).tolist())

        self.assertIn("行情已是最新", text)
        self.assertIn("进入候选池", text)
        self.assertNotIn("up_to_date", text)
        self.assertNotIn("selected", text)
        self.assertNotIn("nan", text.lower())
        self.assertNotIn("None", text)

    def test_v21_status_translation_covers_required_internal_codes(self) -> None:
        frame = _v21_frame(
            [
                {
                    "candidate": "selected",
                    "cache": "up_to_date",
                    "action": "WATCH",
                    "mode": "DRAFT",
                    "confirm": "MANUAL_CONFIRM",
                    "reason": "fallback_reason: qmt_execution 缺失，当前仅输出 DRAFT/MANUAL_CONFIRM 草稿。",
                }
            ],
            {
                "candidate": "候选状态",
                "cache": "行情状态",
                "action": "买入动作",
                "mode": "执行模式",
                "confirm": "确认方式",
                "reason": "降级原因",
            },
        )
        text = " ".join(frame.iloc[0].astype(str).tolist())

        self.assertIn("进入候选池", text)
        self.assertIn("行情已是最新", text)
        self.assertIn("观察，不买入", text)
        self.assertIn("订单草稿", text)
        self.assertIn("人工确认", text)
        self.assertIn("降级原因", text)
        self.assertNotIn("selected", text)
        self.assertNotIn("up_to_date", text)
        self.assertNotIn("WATCH", text)
        self.assertNotIn("DRAFT", text)
        self.assertNotIn("MANUAL_CONFIRM", text)

    def test_price_validation_copy_says_data_not_buy_signal(self) -> None:
        self.assertIn("数据可信，不等于买入信号", _v21_display_value("校验通过：数据可信，不等于买入信号"))

    def test_v2_long_summary_builds_separate_tables_with_reason_preview(self) -> None:
        row = pd.Series({"modular_candidate_etfs": "159915 创业板ETF、510300 沪深300ETF", "modular_selected_sectors": "成长"})
        cases = pd.DataFrame(
            [
                {
                    "trade_date": "2026-05-19",
                    "etf_code": "159915",
                    "etf_name": "创业板ETF",
                    "level1_sector": "成长",
                    "etf_rank": "1",
                    "entry_action": "观察",
                    "target_weight": "0",
                    "confidence": "0.32",
                    "reason": "这是一个很长的候选原因，用于确认页面不会把所有内容塞进两列表格里导致横向滚动。",
                }
            ]
        )

        candidate = build_v2_candidate_table(row, cases=cases)
        actions = build_v2_action_table("159915:观察 | 510300:标准买入", action_label="买入动作")

        self.assertIn("原因摘要", candidate.columns)
        self.assertIn("完整原因", candidate.columns)
        self.assertEqual(candidate.iloc[0]["买入动作"], "候选观察（不是买入）")
        self.assertEqual(actions["ETF代码"].tolist(), ["159915", "510300"])
        self.assertTrue(build_v2_action_table("无", action_label="买入动作").empty)

    def test_v2_buy_plan_fills_name_from_pre_selection_when_entry_name_missing(self) -> None:
        entry = pd.DataFrame(
            [{"symbol": 560780, "buy_action": "观察", "position_size": "0", "confidence": "0.32", "entry_reason": "entry reason"}]
        )
        pre_selection = pd.DataFrame([{"symbol": "560780", "name": "半导体设备ETF广发", "sector": "半导体设备"}])
        lookup = build_v2_etf_lookup(entry=entry, pre_selection=pre_selection, cases=pd.DataFrame(), etf_names={})

        table = build_v2_action_table("560780:观察", action_label="买入动作", lookup=lookup)

        self.assertEqual(table.iloc[0]["ETF代码"], "560780")
        self.assertEqual(table.iloc[0]["ETF名称"], "半导体设备ETF广发")
        self.assertEqual(table.iloc[0]["入选板块"], "半导体设备")
        self.assertEqual(table.iloc[0]["买入动作"], "候选观察（不是买入）")

    def test_v2_candidate_and_buy_tables_show_ml_observation_fields(self) -> None:
        entry = pd.DataFrame(
            [
                {
                    "symbol": "159915",
                    "name": "创业板ETF",
                    "buy_action": "观察",
                    "position_size": "0",
                    "confidence": "0.32",
                    "entry_reason": "entry reason",
                    "ml_entry_advice": "建议等待回踩",
                    "ml_confidence": "0.73",
                    "ml_reason": "历史样本显示当前乖离偏高。",
                    "ml_action_suggestion": "WAIT_PULLBACK",
                }
            ]
        )
        cases = pd.DataFrame(
            [
                {
                    "trade_date": "2026-05-19",
                    "etf_code": "159915",
                    "etf_name": "创业板ETF",
                    "level1_sector": "成长",
                    "entry_action": "观察",
                    "target_weight": "0",
                    "confidence": "0.32",
                    "reason": "候选观察",
                    "ml_entry_advice": "建议等待回踩",
                    "ml_confidence": "0.73",
                    "ml_reason": "历史样本显示当前乖离偏高。",
                    "ml_action_suggestion": "WAIT_PULLBACK",
                }
            ]
        )
        lookup = build_v2_etf_lookup(entry=entry, cases=cases, etf_names={})

        candidate = build_v2_candidate_table(pd.Series({}), cases=cases, lookup=lookup)
        buy = build_v2_action_table("159915:观察", action_label="买入动作", lookup=lookup)

        for frame in (candidate, buy):
            self.assertIn("ML观察建议", frame.columns)
            self.assertIn("ML置信度", frame.columns)
            self.assertIn("ML原因", frame.columns)
            self.assertIn("ML动作建议", frame.columns)
            self.assertIn("仅供观察，不自动修改交易参数。", frame.iloc[0].astype(str).to_string())
        self.assertEqual(candidate.iloc[0]["ML观察建议"], "建议等待回踩")
        self.assertEqual(buy.iloc[0]["ML动作建议"], "WAIT_PULLBACK")

    def test_v2_name_matching_handles_numeric_and_string_codes_consistently(self) -> None:
        entry = pd.DataFrame([{"symbol": 159558, "name": ""}])
        cases = pd.DataFrame([{"etf_code": "159558", "etf_name": "半导体设备ETF易方达", "level1_sector": "半导体设备"}])
        lookup = build_v2_etf_lookup(entry=entry, cases=cases, etf_names={})

        candidate = build_v2_candidate_table(pd.Series({}), cases=cases, lookup=lookup)
        buy = build_v2_action_table("159558:观察", action_label="买入动作", lookup=lookup)

        self.assertEqual(candidate.iloc[0]["ETF名称"], buy.iloc[0]["ETF名称"])
        self.assertEqual(buy.iloc[0]["ETF名称"], "半导体设备ETF易方达")

    def test_v2_missing_name_uses_explicit_unmatched_label(self) -> None:
        lookup = build_v2_etf_lookup(entry=pd.DataFrame([{"symbol": "999999", "buy_action": "观察"}]), etf_names={})

        table = build_v2_action_table("999999:观察", action_label="买入动作", lookup=lookup)
        text = " ".join(table.iloc[0].astype(str).tolist())

        self.assertIn("名称未匹配", text)
        self.assertNotIn("未记录", text)

    def test_portfolio_save_round_trips_after_one_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            portfolio_path = Path(tmp) / "portfolio.csv"
            position_path = Path(tmp) / "current_position.yaml"
            save_portfolio(
                [{"symbol": "159915", "name": "创业板ETF", "shares": 100.0, "average_buy_price": 2.5, "last_buy_date": "2026-05-19"}],
                cash=1000.0,
                current_empty=False,
                portfolio_path=portfolio_path,
                current_position_path=position_path,
            )

            saved = load_portfolio(portfolio_path)

        self.assertEqual(saved.iloc[0]["ETF代码"], "159915")
        self.assertEqual(float(saved.iloc[0]["持仓份额"]), 100.0)
        self.assertEqual(float(saved.iloc[0]["平均买入价"]), 2.5)

    def test_dashboard_loader_is_display_only_and_v1_is_historical_reference(self) -> None:
        import inspect
        import app

        loader_source = inspect.getsource(load_dashboard_data)
        page_source = inspect.getsource(app.render_page)

        self.assertNotIn("run_project_command", loader_source)
        self.assertNotIn("generate-signal", loader_source)
        self.assertIn("今日总览", page_source)
        self.assertIn("V1 对照", page_source)
        self.assertIn("V1 传统信号，仅用于对照", page_source)
        self.assertNotIn("render_sidebar", page_source)

    def test_sidebar_has_visible_expand_and_collapse_labels(self) -> None:
        import inspect
        import app

        toggle_source = inspect.getsource(app.render_sidebar_toggle)

        self.assertIn("展开侧栏", toggle_source)
        self.assertIn("收起侧栏", toggle_source)

    def test_hindsight_status_explains_all_insufficient_samples(self) -> None:
        text = build_hindsight_sample_status(
            pd.DataFrame(
                [
                    {"trade_date": "2026-05-19", "hindsight_label": "样本不足"},
                    {"trade_date": "2026-05-19", "hindsight_label": "样本不足"},
                ]
            )
        )

        self.assertIn("病例库尚在积累期", text)
        self.assertIn("1/3/5/10", text)


if __name__ == "__main__":
    unittest.main()
