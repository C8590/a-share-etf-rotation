# ETF 行情复权口径与缓存元数据审计说明

## 当前下载口径

当前下载链路会按顺序尝试以下 AKShare 接口：

1. `akshare.fund_etf_hist_sina`
2. `akshare.fund_etf_hist_em(adjust="qfq")`
3. `akshare.fund_etf_hist_em(adjust="")`，在审计中记为 `none`

`fund_etf_hist_em(adjust="qfq")` 明确请求前复权；`fund_etf_hist_em(adjust="")` 表示未请求复权处理；`fund_etf_hist_sina` 当前不能从缓存中确认复权口径，因此记为 `unknown`。

## 为什么需要 sidecar metadata

行情 CSV 主要承载价格序列，适合被回测、信号和质量检查直接读取；但复权口径、接口名称、fallback 链路、下载时间、缓存版本等信息属于“数据来源元数据”，不应该只靠价格表里的 `source` 字段间接推断。

因此新下载成功写入 `data/cache/{symbol}.csv` 时，会同步写入 `data/cache_meta/{symbol}.json`。sidecar metadata 让每只 ETF 的缓存都可以回答：

- 来自哪个 source、api 和 endpoint。
- 明确的 `adjust` 是 `qfq`、`none` 还是 `unknown`。
- 是否发生 fallback，以及 fallback_chain 是什么。
- 当前 metadata 对应哪个 cache_file、多少行、覆盖哪段日期。
- 元数据和价格数据的 schema 版本。

这不是为了放宽 QA，而是为了让后续审计、刷新和人工复核有可追踪证据。

## 旧缓存不能假装知道复权口径

ETF-GAP-003 之前的缓存大多只有 CSV，没有 sidecar metadata。即使 CSV 里存在 `source=akshare.fund_etf_hist_sina`，系统也不能反推出它一定是前复权、后复权或不复权。

因此旧缓存如果没有 `data/cache_meta/{symbol}.json`，会被标记为：

- `adjust=unknown`
- `audit_status=warning_unknown_adjustment`
- `audit_reason` 包含 `legacy cache without metadata`

这类结果表示“缓存可读，但复权口径没有被历史记录证明”。系统不会因此删除缓存，也不会直接把它判成硬失败。

## 新缓存如何记录 source / adjust / fallback

新下载落盘时会写入 metadata，核心字段包括：

- `symbol`、`name`
- `source`
- `adjust`
- `api_name`
- `endpoint`
- `download_method`
- `fallback_used`
- `fallback_chain`
- `cache_file`
- `downloaded_at`
- `start_date`、`end_date`
- `row_count`
- `cache_schema_version`
- `data_schema_version`
- `created_by`

映射规则：

- `akshare.fund_etf_hist_em(adjust="qfq")` 或 `akshare.fund_etf_hist_em.qfq`：`adjust=qfq`
- `akshare.fund_etf_hist_em(adjust="")` 或 `akshare.fund_etf_hist_em.none`：`adjust=none`
- `akshare.fund_etf_hist_sina`：`adjust=unknown`

如果首选接口失败后使用了后续接口，`fallback_used=true`，并在 `fallback_chain` 中记录实际尝试链路。没有 fallback 时，`fallback_used=false`。

## cache_metadata_audit.csv 如何解释

`output/cache_metadata_audit.csv` 每只 ETF 一行，用来检查价格缓存与 sidecar metadata 的关系。

常见状态：

- `ok`：缓存存在，metadata 存在，行数和文件指向匹配，且复权口径明确。
- `warning_legacy_cache_without_metadata`：缓存存在但没有 metadata，是历史缓存。
- `warning_unknown_adjustment`：metadata 存在，但 `adjust=unknown`。
- `warning_metadata_cache_mismatch`：metadata 与缓存不一致，例如 `row_count` 或 `cache_file` 不匹配。
- `error_missing_cache`：ETF 池中有该标的，但本地缓存不存在。
- `unknown`：metadata 无法读取或状态无法归类。

## adjustment_audit.csv 的判断逻辑

`output/adjustment_audit.csv` 会优先读取 `data/cache_meta/{symbol}.json`。如果 metadata 存在，`source`、`adjust`、`download_method` 和 `fallback_used` 以 metadata 为准；CSV 仍用于统计日期范围、行数和异常收益。

如果 metadata 不存在但 CSV 存在，审计不会再从旧 CSV 的 `source` 字段假装推断明确复权口径，而是统一记录为 legacy unknown。

`abnormal_return` 不一定等于坏数据。它可能来自分红、拆分、份额折算、真实市场波动、接口口径差异或缓存合并问题。因此系统只把它暴露为审计风险，不手工修改价格，也不放宽 QA。

## 必须人工确认的情况

以下情况需要人工复核，不能只靠程序自动判断：

- `warning_metadata_cache_mismatch`
- `adjust=unknown` 且该 ETF 被策略选中或进入重点观察名单
- `warning_abnormal_return` 且 `possible_adjustment_issue=True`
- fallback 到 `none` 后出现异常收益
- 分红、拆分、份额折算或数据源公告可以解释价格跳变，但系统尚未接入对应事件数据
- metadata 缺失、损坏或与缓存文件无法对应

## 如何安全刷新旧缓存

后续如果要治理旧缓存，应分批执行，而不是一次性重下全市场数据：

1. 先用 `qa-check` 生成 `cache_metadata_audit.csv` 和 `adjustment_audit.csv`。
2. 按 `warning_legacy_cache_without_metadata`、`warning_unknown_adjustment`、`warning_metadata_cache_mismatch` 分组。
3. 优先挑选策略实际使用、异常收益明显、或 metadata/cache 不匹配的 ETF。
4. 对小批量 symbol 执行 refresh 或 rebuild，并确认新写入的 `data/cache_meta/{symbol}.json`。
5. 重新运行单元测试和 `main.py qa-check`，比较刷新前后的审计结果。
6. 只有在人工确认口径和数据质量后，才扩大刷新范围。

刷新旧缓存的目标是补齐可追踪元数据，不是为了让 QA 静默通过，也不是为了手工修补价格序列。

## ETF-GAP-003F: EM qfq source preference evaluation

`python main.py eval-source-preference` is an audit-only experiment for comparing:

- `sina_unknown`: `akshare.fund_etf_hist_sina`, adjustment cannot be proven from the API path.
- `em_qfq`: `akshare.fund_etf_hist_em(adjust="qfq")`, explicit front-adjusted request.
- `em_none`: `akshare.fund_etf_hist_em(adjust="")`, explicit no-adjustment request.

The purpose is to decide whether EM qfq is a better future preferred source than Sina for ETF history. It does not change the downloader priority, does not rewrite `data/cache/*.csv`, does not rewrite `data/cache_meta/*.json`, and does not loosen any QA gate.

EM qfq can be considered promotable only when it downloads successfully, has enough rows, passes schema and quality checks, is not less recent than Sina, and does not show unexplained overlap differences versus Sina. If EM qfq has materially fewer rows, worse freshness, missing fields, large missing values, abnormal returns, or materially different overlapping close/return behavior, it must remain a manual review item.

`em_none` is useful as diagnostic evidence, but it should not outrank `em_qfq` when qfq is healthy. Sina remains the fallback when EM qfq fails, has shorter history, or cannot be reconciled against existing overlap.

The report `output/source_preference_audit.csv` should be read per symbol across the three candidate rows. `preferred_candidate` is the audit recommendation, `safe_to_promote=True` is only assigned to the preferred candidate row, and `requires_manual_review=True` means the symbol should not be used for an automatic source switch.

## ETF-GAP-003F-A: EM connectivity diagnostics

The current ETF-GAP-003F result is explicit: Sina remains the current usable primary history path, while EM qfq and EM none are candidate paths only. In the latest source-preference sample, Sina succeeded and both EM variants failed through `push2his.eastmoney.com` proxy/remote-close errors. This is not evidence that EM qfq has poor adjustment quality; it is evidence that the EM endpoint is not reachable reliably in the current network or proxy environment.

`python main.py diagnose-source --symbols 510300,159915,560320` runs a diagnostic-only check. It does not change downloader priority, does not write `data/cache/*.csv`, does not write `data/cache_meta/*.json`, and does not loosen any QA gate. The report `output/source_diagnostics_report.csv` records:

- `akshare_sina`: whether the current Sina path is reachable.
- `akshare_em_qfq`: whether AKShare can call `fund_etf_hist_em(adjust="qfq")`.
- `akshare_em_none`: whether AKShare can call `fund_etf_hist_em(adjust="")`.
- `raw_endpoint_probe`: whether the underlying EastMoney kline endpoint responds outside AKShare normalization.
- `proxy_env`: whether local proxy environment variables are present.

EM qfq can be reconsidered only after diagnostics show the EM path is reachable and the preserved `error_type` / `error_message` no longer point to proxy, timeout, HTTP, or AKShare parameter failures. After that, rerun `python main.py eval-source-preference --max-count 12`; only a materially better `em_qfq_success_count` and `em_qfq_safe_to_promote_count` can justify moving to ETF-GAP-003G.

Do not abandon adjustment-governance work just because EM currently fails. A failed explicit-qfq candidate still teaches us where the data-source reliability problem is. The correct action is to keep Sina as the current usable path, keep EM qfq as a candidate, diagnose reachability, and continue preserving source/adjustment metadata rather than pretending the adjustment question no longer matters.
