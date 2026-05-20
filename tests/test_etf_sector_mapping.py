from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

from data.daily_export import build_etf_daily_frame
from data.sector_map import apply_sector_mapping, load_etf_sector_map, lookup_sector_mapping, validate_sector_mapping
from data.storage import save_etf_data


REPO_ROOT = Path(__file__).resolve().parents[1]


def _price_frame(start: str = "2026-05-11", periods: int = 2) -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=periods)
    return pd.DataFrame(
        {
            "date": dates,
            "open": [1.0 + i * 0.01 for i in range(periods)],
            "high": [1.1 + i * 0.01 for i in range(periods)],
            "low": [0.9 + i * 0.01 for i in range(periods)],
            "close": [1.05 + i * 0.01 for i in range(periods)],
            "volume": [1000 + i for i in range(periods)],
            "amount": [10000 + i * 10 for i in range(periods)],
        }
    )


class EtfSectorMappingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        Path("config").mkdir()
        self.map_path = Path("config/etf_sector_map.yaml")
        self.map_path.write_text(
            yaml.safe_dump(
                {
                    "etfs": [
                        {
                            "code": "510300",
                            "name": "沪深300ETF",
                            "asset_class": "权益",
                            "sector": "大盘宽基",
                            "sector_l1": "宽基指数",
                            "sector_l2": "大盘宽基",
                            "theme": "沪深300",
                            "risk_group": "全市场",
                            "aliases": ["沪深300", "宽基"],
                            "is_defensive": False,
                            "is_broad_market": True,
                        },
                        {
                            "code": "512480",
                            "name": "半导体ETF",
                            "asset_class": "权益",
                            "sector": "科技成长",
                            "sector_l1": "行业主题",
                            "sector_l2": "科技成长",
                            "theme": "半导体",
                            "risk_group": "半导体",
                            "aliases": ["半导体", "芯片"],
                            "is_defensive": False,
                            "is_broad_market": False,
                        },
                    ]
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_known_etf_can_be_read_with_name_sector_and_theme(self) -> None:
        mapping = load_etf_sector_map(self.map_path)

        self.assertEqual(set(mapping), {"510300", "512480"})
        self.assertEqual(mapping["510300"]["name"], "沪深300ETF")
        self.assertEqual(mapping["510300"]["sector_l2"], "大盘宽基")
        self.assertEqual(mapping["510300"]["theme"], "沪深300")
        self.assertEqual(mapping["512480"]["risk_group"], "半导体")

    def test_code_formats_share_one_normalized_mapping(self) -> None:
        for raw_code in ["510300", 510300, "SH510300", "510300.SH", " 510300 "]:
            record = lookup_sector_mapping(raw_code, self.map_path)
            self.assertEqual(record["code"], "510300")
            self.assertEqual(record["theme"], "沪深300")

    def test_unknown_etf_returns_chinese_defaults_and_warns(self) -> None:
        with self.assertWarnsRegex(UserWarning, "行业映射缺失 1 个代码"):
            pool = apply_sector_mapping([{"symbol": "159999"}], self.map_path)

        self.assertEqual(pool[0]["name"], "名称未录入")
        self.assertEqual(pool[0]["asset_class"], "资产类别未录入")
        self.assertEqual(pool[0]["sector_l2"], "行业未录入")
        self.assertEqual(pool[0]["theme"], "主题未录入")
        quality = validate_sector_mapping(pool)
        self.assertEqual(quality.unknown_count, 1)
        self.assertIn("ETF 映射未录入", quality.warnings[0])

    def test_core_universe_codes_are_mapped(self) -> None:
        universe = yaml.safe_load((REPO_ROOT / "config" / "etf_universe.yaml").read_text(encoding="utf-8"))
        core_codes = universe["presets"]["core_11"]["symbols"]
        mapping = load_etf_sector_map(REPO_ROOT / "config" / "etf_sector_map.yaml")
        missing = [code for code in core_codes if str(code) not in mapping]

        self.assertLessEqual(len(missing), 1, f"核心 ETF 映射缺失过多：{missing}")

    def test_daily_export_contains_sector_mapping_columns(self) -> None:
        save_etf_data("510300", _price_frame(), data_dir=Path("cache"), name="沪深300ETF", source="seed")
        frame = build_etf_daily_frame(
            etf_pool=[{"symbol": "510300"}],
            data_dir=Path("cache"),
        )

        for column in [
            "name",
            "asset_class",
            "sector",
            "sector_l1",
            "sector_l2",
            "theme",
            "risk_group",
            "aliases",
            "is_defensive",
            "is_broad_market",
        ]:
            self.assertIn(column, frame.columns)
        self.assertEqual(set(frame["name"]), {"沪深300ETF"})
        self.assertEqual(set(frame["sector_l2"]), {"大盘宽基"})
        self.assertEqual(set(frame["theme"]), {"沪深300"})
        self.assertEqual(set(frame["risk_group"]), {"全市场"})
        self.assertEqual(set(frame["is_defensive"]), {"否"})
        self.assertEqual(set(frame["is_broad_market"]), {"是"})

    def test_daily_export_has_no_frontend_unfriendly_values(self) -> None:
        save_etf_data("512480", _price_frame(), data_dir=Path("cache"), name="半导体ETF", source="seed")
        frame = build_etf_daily_frame(
            etf_pool=[{"symbol": "SZ512480"}],
            data_dir=Path("cache"),
        )

        forbidden = {"none", "nan", "true", "false", "unknown"}
        values = {str(value).strip().lower() for value in frame.astype(str).to_numpy().ravel()}
        self.assertTrue(values.isdisjoint(forbidden))


if __name__ == "__main__":
    unittest.main()
