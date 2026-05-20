from __future__ import annotations

import argparse
from pathlib import Path

from .audit import generate_replay_audit_report
from .calibration import generate_entry_calibration_outputs
from .config import HistoricalMLConfig
from .io_utils import read_price_data, read_table, write_table
from .labeler import FutureLabeler
from .replay_engine import HistoricalReplayEngine
from .reports import generate_entry_threshold_report
from .review_queue import build_manual_review_queue


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="historical_ml sample factory")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--prices", required=True, help="ETF daily price CSV/parquet: date,code,name,close,sector,...")
        sp.add_argument("--start", default="2024-09-24")
        sp.add_argument("--end", default="2026-05-19")
        sp.add_argument("--out", required=True)
        sp.add_argument("--format", choices=["csv", "parquet"], default="csv")
        sp.add_argument("--market-code", default=None)

    replay = sub.add_parser("replay", help="produce daily feature/candidate samples without future labels")
    add_common(replay)

    label = sub.add_parser("label", help="attach future labels to entry_candidate_samples")
    label.add_argument("--prices", required=True)
    label.add_argument("--samples", required=True)
    label.add_argument("--out", required=True)
    label.add_argument("--format", choices=["csv", "parquet"], default="csv")
    label.add_argument("--market-code", default=None)

    report = sub.add_parser("report", help="generate manual review queue and entry threshold report")
    report.add_argument("--labeled-samples", required=True)
    report.add_argument("--daily-etf-samples", default=None)
    report.add_argument("--daily-sector-samples", default=None)
    report.add_argument("--daily-decision-snapshot", default=None)
    report.add_argument("--unlabeled-samples", default=None)
    report.add_argument("--out", required=True)
    report.add_argument("--format", choices=["csv", "parquet"], default="csv")

    run_all = sub.add_parser("run-all", help="replay + label + review queue + threshold report")
    add_common(run_all)
    return p


def _config_from_args(args) -> HistoricalMLConfig:
    return HistoricalMLConfig(
        replay_start=__import__("datetime").date.fromisoformat(args.start) if hasattr(args, "start") else HistoricalMLConfig().replay_start,
        replay_end=__import__("datetime").date.fromisoformat(args.end) if hasattr(args, "end") else HistoricalMLConfig().replay_end,
        output_format=getattr(args, "format", "csv"),
        market_index_code=getattr(args, "market_code", None),
    )


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "replay":
        config = _config_from_args(args)
        prices = read_price_data(args.prices)
        engine = HistoricalReplayEngine(prices, config=config)
        engine.run(config.replay_start, config.replay_end, out_dir=out_dir)
        return 0

    if args.command == "label":
        config = HistoricalMLConfig(output_format=args.format, market_index_code=args.market_code)
        prices = read_price_data(args.prices)
        samples = read_table(args.samples)
        labeled = FutureLabeler(prices, config=config).attach_labels(samples)
        write_table(labeled, out_dir, "entry_candidate_samples_labeled", args.format)
        return 0

    if args.command == "report":
        config = HistoricalMLConfig(output_format=args.format)
        labeled = read_table(args.labeled_samples)
        review = build_manual_review_queue(labeled, config=config)
        write_table(review, out_dir, "manual_review_queue", args.format)
        audit_inputs = {
            "daily_etf_samples": read_table(args.daily_etf_samples) if args.daily_etf_samples else None,
            "daily_sector_samples": read_table(args.daily_sector_samples) if args.daily_sector_samples else None,
            "daily_decision_snapshot": read_table(args.daily_decision_snapshot) if args.daily_decision_snapshot else None,
            "entry_candidate_samples_unlabeled": read_table(args.unlabeled_samples) if args.unlabeled_samples else None,
        }
        audit_inputs = {k: v for k, v in audit_inputs.items() if v is not None}
        if audit_inputs:
            generate_replay_audit_report(audit_inputs, labeled, out_dir / "replay_audit_report.md", config=config)
        generate_entry_threshold_report(labeled, out_dir / "entry_threshold_report.md", config=config)
        generate_entry_calibration_outputs(labeled, out_dir, config=config)
        return 0

    if args.command == "run-all":
        config = _config_from_args(args)
        prices = read_price_data(args.prices)
        engine = HistoricalReplayEngine(prices, config=config)
        outputs = engine.run(config.replay_start, config.replay_end, out_dir=out_dir)
        labeled = FutureLabeler(prices, config=config).attach_labels(outputs["entry_candidate_samples"])
        write_table(labeled, out_dir, "entry_candidate_samples_labeled", config.output_format)
        review = build_manual_review_queue(labeled, config=config)
        write_table(review, out_dir, "manual_review_queue", config.output_format)
        audit_outputs = {
            "daily_etf_samples": outputs["daily_etf_samples"],
            "daily_sector_samples": outputs["daily_sector_samples"],
            "daily_decision_snapshot": outputs["daily_decision_snapshot"],
            "entry_candidate_samples_unlabeled": outputs["entry_candidate_samples"],
        }
        generate_replay_audit_report(audit_outputs, labeled, out_dir / "replay_audit_report.md", config=config)
        generate_entry_threshold_report(labeled, out_dir / "entry_threshold_report.md", config=config)
        generate_entry_calibration_outputs(labeled, out_dir, config=config)
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
