# historical_ml 规格书

## 1. 目标

用历史数据回放生成可分析、可标注、可训练的 entry 样本库，帮助 entry 项目部提升买入精准度。

historical_ml 不直接改 entry，不接 QMT，不给实时买卖建议。

## 2. 回放流程

```text
历史价格数据
  -> DayReplay(D)
      -> 只取 date <= D 的数据
      -> ETF 特征 daily_etf_samples
      -> 板块特征 daily_sector_samples
      -> entry adapter 生成候选/选中/买入标记
      -> daily_decision_snapshot
      -> entry_candidate_samples_unlabeled
  -> FutureLabeler
      -> 使用 execution_date 作为标签起点
      -> 生成未来 1/3/5/10/20 日表现标签
  -> ReviewQueue
  -> entry_threshold_report
```

## 3. 无未来函数守卫

- `feature_builder` 只能读取 `date <= trade_date`。
- `labeler` 只能在 replay 输出后运行。
- 测试通过扰动未来价格确认当日特征不变。

## 4. 信号日与执行日

- `trade_date = signal_date`
- `execution_date = next_trading_date(signal_date)`
- 未来表现标签从 `execution_date` 开始计算。

## 5. 样本表

### daily_etf_samples

逐日、逐 ETF 特征。用于分析 entry score、动量、加速度、趋势成熟度、流动性、风险。

### daily_sector_samples

逐日、逐板块特征。用于分析板块状态和板块拥挤。

### daily_decision_snapshot

逐日决策快照。用于检查候选数、选中数、买入数、过滤数和异常数。

### entry_candidate_samples

逐日 entry 训练样本。包含 was_candidate、was_selected、was_bought 与 exclude_reason。

## 6. 自动标签

- `future_return_1d/3d/5d/10d/20d`
- `future_max_gain_10d`
- `future_max_drawdown_10d`
- `outperform_market_10d`
- `outperform_sector_10d`
- `exit_within_3d`
- `auto_label`

默认标签逻辑：

```text
good_entry:
  future_return_10d >= 4%
  and future_max_drawdown_10d > -5%
  and outperform_market_10d or outperform_sector_10d

bad_entry:
  future_return_10d <= -3%
  or future_max_drawdown_10d <= -5%
  or exit_within_3d

otherwise:
  neutral_entry
```

## 7. 人工复核队列

只挑重点样本，不标注全量：

- large_loss_entry
- quick_failure_entry
- bought_and_knocked_out
- missed_big_winner
- top_rank_filtered
- same_sector_skipped
- data_abnormal

## 8. entry 校准报告问题清单

报告必须回答：

- 哪些 entry 特征成功率高？
- 哪些失败最多？
- 动量分阈值是否合理？
- 加速度权重是否过高？
- 趋势成熟度是否能过滤追高？
- 板块拥挤是否导致失败？
- 不同 market_state 下 entry 是否应该使用不同参数？
