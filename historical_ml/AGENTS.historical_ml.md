# historical_ml 开发约束

## 部门职责

historical_ml 只负责历史回放、样本生产、未来表现标签、人工复核队列、统计报告和参数建议。

禁止：

- 直接修改 entry 交易规则。
- 直接生成实时买卖建议。
- 接 QMT。
- 处理实时风险事件。
- 在 replay 特征阶段使用未来数据。

## 强制要求

1. 每个历史交易日只能使用该日及之前数据。
2. `signal_date` 与 `execution_date` 必须分离。
3. 未来标签必须在 replay 完成后单独生成。
4. 所有输出样本必须带 `source=historical_replay`。
5. 最后 20 个交易日若没有足够未来数据，不得强行打 20 日标签；使用 `label_status=insufficient_future_data`。
6. 新增逻辑必须有测试覆盖：字段完整、日期完整、无未来函数、信号/执行日分离。

## 推荐目录

```text
historical_ml/
  config.py
  schemas.py
  feature_builder.py
  entry_adapter.py
  replay_engine.py
  labeler.py
  review_queue.py
  reports.py
  validators.py
  cli.py
```

## 验收命令

```bash
python -m historical_ml.cli run-all \
  --prices data/etf_daily.csv \
  --start 2024-09-24 \
  --end 2026-05-19 \
  --out artifacts/historical_ml

pytest -q
```
