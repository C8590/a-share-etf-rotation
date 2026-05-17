# 人工复核证据包说明

本说明用于 `output/manual_review_evidence_pack.csv` 和
`output/manual_review_decision_template.csv`。

人工复核不是判断 ETF 好坏，也不是给 ETF 打分。它只回答一个更窄的问题：
当前是否有足够证据解除 `manual_review_required` 阻断。

如果不会复核、证据不足、异常无法解释，默认结论就是继续阻断。

## 本轮结论

当前 5 只 P0 人工复核 ETF 全部建议继续阻断：

- 159231 通用航空ETF华宝
- 159246 创业板人工智能ETF富国
- 159287 创业板综ETF博时
- 159387 创业板新能源ETF国泰
- 560320 N食品ETF富国

本轮不允许把任何一只改成 eligible，不解除人工复核阻断，不修改候选池门禁。

## 可以考虑解除的条件

只有同时满足以下条件时，才可以考虑在未来流程中解除阻断：

- 历史数据已经满足最小样本长度要求。
- 异常收益已经有可信解释，例如真实市场事件、复权口径确认或数据源一致性验证。
- 流动性证据可接受，成交额和零成交情况不再构成候选池风险。
- 复核结论有明确记录，并可追溯到具体证据。
- 复核后重新运行数据诊断、观察池、候选池门禁，而不是直接改候选结果。

## 必须继续阻断的情况

出现以下任一情况，应继续阻断：

- 仍是 `very_short_history` 或历史样本明显不足。
- 异常收益无法解释。
- 低流动性或零成交证据仍明显。
- 证据包字段缺失，不能确认问题来源。
- 复核人无法独立判断。
- 只是因为“不想阻断”而没有证据支持。

## 本轮默认模板

`manual_review_decision_template.csv` 的默认值是：

- `review_decision = keep_blocked`
- `review_status = blocked_until_review`
- `unblock_allowed = false`
- `required_future_condition = sufficient history + anomaly explained + liquidity acceptable`

这表示证据包已经整理好，但没有任何自动放行含义。

## 安全边界

本流程只读取已有报告和本地 cache 计算证据摘要，只写两个报告文件：

- `output/manual_review_evidence_pack.csv`
- `output/manual_review_decision_template.csv`

它不刷新 ETF cache，不刷新 index cache，不改 `candidate_gate.csv`，不改策略，
不改回测，不改 UI，不连接券商 API，也不会自动交易。
