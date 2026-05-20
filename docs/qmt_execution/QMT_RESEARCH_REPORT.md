# QMT / miniQMT / XtQuant 调研报告（第一阶段）

> 目标：建立安全的交易执行适配器。第一阶段只允许模拟盘、半自动执行、订单草稿和回报同步；禁止直接实盘全自动下单。

## 1. 接口能力矩阵

| 能力 | 初步结论 | 接入方式 | 第一阶段处理 |
|---|---|---|---|
| ETF / 场内基金 | 支持行情与场内基金列表；二级市场买卖按证券代码走 `order_stock`；ETF 申赎属于额外能力，暂不接 | `xtdata.get_stock_list_in_sector('沪深基金')`；`order_stock` | 只做二级市场 ETF BUY/SELL，不做申赎 |
| 模拟盘 / 仿真 | QMT 模型交易有模拟模式；券商/测试柜台支持情况要逐券商确认 | QMT 客户端模式 / 券商测试环境 | 默认 MockBroker；真实 QMT 仿真另开配置 |
| Python 外部调用 | 支持；XtQuant 是 Python API，与 MiniQMT 交互 | `xtquant.xttrader` / `xtquant.xtdata` | QmtAdapter 只做骨架，默认不提交 |
| 下单 | 支持同步/异步股票报单 | `order_stock` / `order_stock_async` | 默认禁止；仅 SIM + 人工确认 + 开关开启才可调 |
| 撤单 | 支持按 order_id / order_sysid 撤单 | `cancel_order_stock` / `cancel_order_stock_sysid` | SIM 可接；LIVE 强拦截 |
| 查询资金 | 支持 | `query_stock_asset` | 接入持仓同步前置 |
| 查询持仓 | 支持 | `query_stock_positions` | 回写独立持仓系统 |
| 查询委托 | 支持 | `query_stock_orders` | 订单回放与重复单检查 |
| 查询成交 | 支持 | `query_stock_trades` | 成交同步 |
| 实时回报推送 | 支持 | callback + `subscribe(account)` | 订单/成交/错误事件统一入日志 |
| 频率限制 | 官方 API 文档未见固定数值；交易所程序化监管和券商柜台风控约束必须遵守 | 交易所规则 + 券商风控 | 首版加本地节流、重复单锁、日内笔数计数 |
| 券商限制 | 需要券商开通 QMT/miniQMT 权限，且功能/门槛/品种可能不同 | 客户经理 / 券商测试 | 建立券商白名单配置 |
| Windows 客户端常驻 | 需要 MiniQMT / XtMiniQmt 运行；非 Windows 可通过桥接服务间接访问，但不建议第一阶段交易 | Windows + QMT 客户端 | 第一阶段仅 Windows 执行机 |

## 2. 第一阶段禁止项

- 禁止 entry / exit 直接调用 QMT。
- 禁止实盘全自动下单。
- 禁止无风控检查下单。
- 禁止无执行日志下单。
- 禁止绕过总控 / P0 / R3 / R4。
- 禁止在未同步持仓时下单。

## 3. 第一阶段执行链路

```text
entry / exit
  -> OrderIntent(DRAFT)
  -> ExecutionService
  -> RiskEngine
  -> WAITING_MANUAL_CONFIRM / RISK_REJECTED / READY_TO_SUBMIT
  -> BrokerAdapter(Mock 或 QMT_SIM)
  -> ExecutionLogger(JSONL)
  -> PositionSync
  -> 独立持仓系统
```

## 4. QMT Adapter 安全开关

```text
trading_env = SIM / LIVE
qmt_submit_enabled = false  # 默认
requires_manual_confirm = true  # OrderIntent 默认
```

- `LIVE`：`place_order()` 永远拒绝自动提交。
- `SIM`：只有在 `qmt_submit_enabled=true` 且 `manual_confirmed=true` 时才允许提交。
- Mock：用于第一阶段验收与 CI。

## 5. 实测前 checklist

1. 券商名称、QMT 版本、xtquant 版本、Python 版本。
2. 是否有真实测试柜台或仅“模拟信号模式”。
3. ETF 二级市场买卖最小单位、价格精度、撤单字段。
4. `XtOrder` / `XtTrade` / `XtPosition` 字段名采样。
5. 回报推送是否稳定、断线后是否需要主动重连。
6. 日内申报/撤单频率、券商风控规则、白名单限制。
7. 是否允许 miniQMT 外部 Python 在该账户下程序化委托。
