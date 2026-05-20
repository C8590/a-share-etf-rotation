# aetfv2 historical_ml

`historical_ml` 是 entry 项目部的历史样本生产部门，不直接修改 entry 交易规则、不接 QMT、不生成实时买卖建议。

它负责把 2024-09-24 到 2026-05-19 的 ETF 历史数据逐日回放，生成可分析、可标注、可训练的 entry 样本库，并输出 entry 校准报告。

## 核心边界

- 每个历史交易日只能使用当日及之前数据生成特征。
- 信号日 `signal_date` 与执行日 `execution_date` 分离，默认执行日为下一个交易日。
- 未来收益标签只在 label 阶段生成，不能回流 replay 阶段。
- 不直接修改 entry 规则；生产环境应通过 `EntryAdapter` 调用 entry 项目部的历史决策逻辑。

## 输入数据格式

价格数据为 long format CSV 或 parquet：

| column | required | meaning |
|---|---:|---|
| date | yes | 交易日 |
| code | yes | ETF 代码 |
| name | yes | ETF 名称 |
| close | yes | 收盘价 |
| sector | yes | 二级板块/主题 |
| sector_l1 | no | 一级分类，不传则等于 sector |
| open/high/low | no | 不传则用 close |
| volume/amount | no | 不传则为 0 |

## 一键运行

```bash
python -m historical_ml.cli run-all \
  --prices data/etf_daily.csv \
  --start 2024-09-24 \
  --end 2026-05-19 \
  --out artifacts/historical_ml \
  --format csv
```

## 输出表

- `daily_etf_samples`
- `daily_sector_samples`
- `daily_decision_snapshot`
- `entry_candidate_samples_unlabeled`
- `entry_candidate_samples_labeled`
- `manual_review_queue`
- `entry_threshold_report.md`
- `replay_audit_report.md`

若开启 daily partitions，每天还会写入：

```text
artifacts/historical_ml/daily_etf_samples/trade_date=YYYY-MM-DD/part.csv
artifacts/historical_ml/daily_sector_samples/trade_date=YYYY-MM-DD/part.csv
artifacts/historical_ml/daily_decision_snapshot/trade_date=YYYY-MM-DD/part.csv
artifacts/historical_ml/entry_candidate_samples/trade_date=YYYY-MM-DD/part.csv
```

## entry_candidate_samples 字段

必须字段：

```text
trade_date, code, name, sector, market_state, sector_state,
momentum_score, acceleration_score, entry_score, trend_maturity,
sector_rank, etf_rank, was_candidate, was_selected, was_bought,
exclude_reason, source
```

额外字段：

```text
signal_date, execution_date, sector_l1, global_rank
```

## 未来表现标签

label 阶段生成：

```text
future_return_1d, future_return_3d, future_return_5d,
future_return_10d, future_return_20d,
future_max_gain_10d, future_max_drawdown_10d,
outperform_market_10d, outperform_sector_10d, exit_within_3d,
auto_label
```

`auto_label` 取值：

- `good_entry`
- `bad_entry`
- `neutral_entry`
- `unlabeled`

注意：如果回放结束日是 2026-05-19，但没有其后 20 个交易日数据，严格无未来函数条件下，最后一段样本的 20 日标签会是 `label_status=insufficient_future_data`，不能强行填充。

## 人工复核队列

`manual_review_queue` 自动筛出：

- 大亏 entry
- 快速失败 entry
- 系统买了但很快被打掉的样本
- 系统没买但后来大涨的样本
- 排名靠前但被过滤的样本
- 同板块被跳过的样本
- 数据异常样本

## 接入真实 entry 历史逻辑

实现 `EntryAdapter`：

```python
class RealEntryAdapter:
    def build_entry_candidates(self, etf_samples, sector_samples, signal_date, execution_date, config):
        # 调用 entry 项目部的历史决策逻辑。
        # 返回含 was_candidate / was_selected / was_bought / exclude_reason 的 DataFrame。
        ...
```

`HeuristicEntryAdapter` 只是为了空仓库启动样本流程，不是交易规则。

## 测试

```bash
pytest -q
```
