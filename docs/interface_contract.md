# 交易模块接口合同

本文档只定义四个交易模块之间的文件接口和字段名称，不包含具体策略逻辑，也不要求接入现有 `signal/daily_signal.py`。

## 统一枚举

### 市场状态

- `进攻`
- `均衡`
- `防守`

### 买入动作

- `观察`
- `等待回踩`
- `试探买入`
- `标准买入`
- `加强买入`
- `禁止买入`

### 卖出动作

- `持有`
- `谨慎持有`
- `减仓三分之一`
- `减仓一半`
- `清仓`
- `冷却`

### 失败归因类型

- `买在尾段`
- `买点太差`
- `市场转防守`
- `同板块集中`
- `频繁换仓`
- `卖早`
- `卖晚`
- `数据或流动性问题`

## 模块文件

### 预选模块：`signal/pre_selection/`

输出文件：`pre_selection_result.csv`

| 字段 | 说明 |
| --- | --- |
| `trade_date` | 交易日期 |
| `symbol` | 标的代码 |
| `name` | 标的名称 |
| `sector` | 所属板块或主题 |
| `market_state` | 市场状态，取值见统一枚举 |
| `score` | 预选评分 |
| `rank` | 预选排序 |
| `selected` | 是否进入候选池 |
| `reason` | 入选或排除原因 |
| `generated_at` | 信号生成时间 |

### 买入模块：`signal/entry/`

输入文件：`pre_selection_result.csv`

输出文件：`entry_signal.csv`

| 字段 | 说明 |
| --- | --- |
| `trade_date` | 交易日期 |
| `symbol` | 标的代码 |
| `name` | 标的名称 |
| `market_state` | 市场状态，取值见统一枚举 |
| `buy_action` | 买入动作，取值见统一枚举 |
| `buy_price` | 参考买入价格 |
| `position_size` | 建议仓位比例 |
| `confidence` | 信号置信度 |
| `entry_reason` | 买入理由 |
| `source_file` | 上游来源文件，通常为 `pre_selection_result.csv` |
| `generated_at` | 信号生成时间 |

### 卖出模块：`signal/exit/`

输入：当前持仓、行情数据、交易上下文。

输出文件：`exit_signal.csv`

| 字段 | 说明 |
| --- | --- |
| `trade_date` | 交易日期 |
| `symbol` | 标的代码 |
| `name` | 标的名称 |
| `market_state` | 市场状态，取值见统一枚举 |
| `sell_action` | 卖出动作，取值见统一枚举 |
| `sell_price` | 参考卖出价格 |
| `reduce_ratio` | 建议减仓比例 |
| `cool_down_days` | 冷却天数 |
| `exit_reason` | 卖出或持有理由 |
| `source_file` | 上游来源文件或持仓来源 |
| `generated_at` | 信号生成时间 |

### 学习模块：`signal/learning/`

输入：已完成交易记录、买入信号、卖出信号、市场状态快照。

输出文件：`learning_report.csv`

| 字段 | 说明 |
| --- | --- |
| `trade_date` | 复盘日期 |
| `trade_id` | 交易记录 ID |
| `symbol` | 标的代码 |
| `name` | 标的名称 |
| `holding_days` | 持仓天数 |
| `return_pct` | 交易收益率 |
| `failure_attribution` | 失败归因类型，取值见统一枚举 |
| `lesson` | 复盘结论 |
| `adjustment` | 后续调整建议 |
| `source_file` | 复盘来源文件 |
| `generated_at` | 报告生成时间 |

## 当前边界

- 本阶段只建立接口合同和目录骨架。
- 不实现预选、买入、卖出或学习策略逻辑。
- 不接入 `signal/daily_signal.py`。
- 不修改现有 `signal/trade_policy.py`、`main.py` 或 `app.py` 的交易逻辑。
