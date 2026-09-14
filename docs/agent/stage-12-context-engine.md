# LearnLoop v2 Stage 12：Context Engine 实现说明

本文记录 Stage 12 的实际实现和架构，重点说明 Agent 每次模型调用“看到了什么、为什么看到、
为什么没有看到”，覆盖 Token budgeting、Artifact、Compaction、provenance 和可观测性。

## 1. 交付结果

Stage 11 的 `MinimalContextCompiler` 已升级为完整 `ContextCompiler`。`dynamic_v2` 的
Planner、Executor 和 Replanner 不再各自拼接输入，而是统一经过：

```text
ContextRequest
  → validate Run / Plan / Step references
  → intersect Step allowlist × runtime permissions × candidate Tools
  → resolve immutable Artifacts Just-in-time
  → validate Artifact version / hash / ownership / expiry
  → deduplicate Observations and detect conflicts
  → reserve output tokens
  → pack Context partitions by priority
  → persist privacy-safe ContextSnapshot
  → model call
  → calibrate estimate with provider input tokens
```

本阶段实现了：

- 结构化 `ContextRequest` 和统一 `ContextPackage`；
- mandatory/optional Context partitions 和 priority policy；
- provider tokenizer adapter 和明确标记的 conservative fallback；
- input budget 与 reserved output budget；
- 基于 Plan Step 的动态 Tool Schema selection；
- Tool result、用户输入和 Agent content 的 immutable Artifact Store；
- Observation 的 Just-in-time Artifact resolution；
- completed Step compaction、重复 Observation 合并、source expiry 和 conflict hint；
- input/output hash、compaction version、coverage 和 source provenance；
- `context_snapshot_created` Trace、查询 API 和前端 Context 面板；
- 默认 metadata-only，完整 Context 仅在显式 local debug 下持久化。

## 2. 架构

```text
DynamicAgentKernel
     ├─ Planner ───────┐
     ├─ Executor ──────┼── ContextRequest
     └─ Replanner ─────┘         │
                                 ▼
                         ContextCompiler
                ┌────────────────┼────────────────┐
                ▼                ▼                ▼
          TokenCounter     ArtifactReader    Source Policy
          exact/fallback   JIT + integrity   TTL/dedup/conflict
                └────────────────┼────────────────┘
                                 ▼
                         ContextPackage
                     values + ContextSnapshot
                           │             │
                           ▼             ▼
                    ModelAgentPolicy   Sqlite Store
```

模型只是 Context consumer。来源选择、Tool 权限、Token 分配、过期处理和完整性验证都由
deterministic Harness 执行，模型不能要求扩大自己的 Context 或权限。

## 3. 统一编译契约

`ContextRequest` 是一次编译的唯一入口：

| 字段 | 作用 |
|---|---|
| `run_id` | 防止跨 Run 引用 Artifact |
| `plan_version / step_id` | 防止 stale Plan Context |
| `objective` | 当前 Session objective |
| `purpose` | `planner / decision / replan` |
| `recent_observation_ids` | Working Context 候选引用 |
| `evidence_ids` | 已验证引用，不能静默丢弃 |
| `memory_query` | Stage 13 前为空 |
| `candidate_tool_names` | 当前调用的 Tool 候选 |
| `token_budget` | 本次 Context window 预算 |
| `reserved_output_tokens` | 模型输出预留空间 |

编译器拒绝 Run、Plan version 或 Step 不匹配的 Request。Evidence 缺失、Artifact 被替换、hash
不匹配或来自其他 Run 时抛出 `ContextReferenceError`，不会把不可重放的 Context 交给模型。

`ContextPackage` 由实际进入 Model Policy 的 `values` 和不包含隐私正文的 `snapshot` 组成。

## 4. Partition policy

不能静默裁剪的 mandatory partitions：

- `policy`：untrusted data、权限由代码控制、禁止 hidden reasoning；
- `objective`、`plan_version` 和 `current_step`；
- `unresolved_items`；
- `budget` 和当前 `usage`；
- `available_tools`；
- 已完成步骤引用的 `evidence`。

如果 mandatory 内容超过 input limit，编译器抛出 `ContextBudgetError`。这比删除唯一合法 Tool、
约束或 evidence 后让模型猜测更安全。

Optional partitions 按优先级加入 `recent_observations`、completed Step compact view 和
`conflicts`。被 budget eviction 的 source 写入 `ContextTruncation`，记录 partition、reason
和 omitted source ids。装箱优先保留最新状态，最终给模型的 Observation 仍保持时间顺序。

## 5. Token budgeting 与 Tool selection

```text
input_token_limit = token_budget - reserved_output_tokens
```

编译器先放 mandatory partitions，再逐条装入 optional item。Tool JSON Schema 会移除
`title/default/examples` 等噪声，但保留类型、required、enum 和权限元数据。候选 Tool Schema
整体属于 mandatory partition，因此不会只因 Schema 较大而静默删除唯一 Action。

每次 Request 还会把 Context 上限与 Run 剩余的 input/output/total Token ledger 求交集；当剩余
output 不足 128 Token 时明确停止，而不是发起一个注定越过 RunBudget 的模型调用。

`token_counter_for(provider)` 优先使用 Provider 的同步 `count_tokens(text)`。当前 DeepSeek
provider 没有 tokenizer API，因此使用 `unicode_chars_div_3` conservative estimate，并在
Snapshot 标记 `exact=false`。Model 返回 usage 后，Store 写入
`observed_model_input_tokens/token_delta`。

`token_delta` 同时包含 Prompt wrapper 和 output Schema，是 operational calibration signal，
不是纯 tokenizer error。

## 6. Artifact Store 与 Just-in-time loading

```text
Tool / user / Agent content
  → canonical JSON → SHA-256 → immutable Context Artifact
  → Observation only stores ArtifactRef
  → ContextCompiler resolves selected refs Just-in-time
```

`ContextArtifactRef` 包含 `artifact_id/kind/version/sha256/created_at/expires_at`。SQLite 使用
`run_id + kind + sha256` 去重，因此 crash replay 不会制造重复对象。读取时验证：

- Artifact 存在且属于当前 Run；
- version 未变化；
- 存储 hash、引用 hash 和实时 canonical hash 一致；
- 可选 `expires_at` 未到期。

完整 Tool result 仍受 `ToolSpec.max_result_chars` 安全边界；“完整”指 Tool Contract 允许进入
Agent 的完整 bounded result，不是无限制复制外部响应。

## 7. Compaction、freshness 与 conflict

Stage 12 使用 deterministic compaction，避免另一个摘要模型引入事实漂移：

- completed Step 压缩为 objective、status、evidence ids 和 attempts；
- 等价 Observation 通过 canonical hash 合并并保留最新值；
- Plan 引用的 evidence 是 protected source，不会被 dedup 删除；
- 非 evidence source 超过 `source_ttl_seconds` 后不进入新 Context；
- 同一 source/field 出现不同 canonical value 时产生 `ContextConflict`；
- `input_hash` 覆盖 Request 和 source version/hash；
- `output_hash` 覆盖实际发送给 Policy 的 Context；
- `compaction_version`、coverage start/end 支持重放和策略升级。

Conflict 只提示冲突及 source ids，不猜测哪个事实正确。Executor 应重读权威 Tool 或请求澄清。

## 8. 持久化、Trace 与隐私

新增表：

| 表 | 内容 |
|---|---|
| `learnloop_context_artifacts` | immutable bounded content、hash、version、expiry |
| `learnloop_context_snapshots` | partition/token/source/tool/hash/compaction metadata |

每次 Planner、Executor 或 Replanner 调用前保存 Snapshot，并产生
`context_snapshot_created` Event。Provider 调用失败时，仍能解释原本选择了哪些来源和 Tool。

默认 `LEARNLOOP_AGENT_CONTEXT_DEBUG_FULL=false`：Snapshot 只保存 metadata，API 不能请求
正文。只有显式开启 debug 后才保存 full Context；
`GET /agent/runs/{id}/trace?include_context=true` 还会再次检查运行配置。Artifact 正文不通过
Trace API 暴露。

## 9. Dynamic Agent 集成

- Planner：bootstrap state 经 `compile_initial()` 与 Tool Schema、Plan 上限形成 Snapshot。
- Executor：每轮为 active Step 构建 Request，Tool candidates 是权限与 Step allowlist 的交集。
- Replanner：使用统一 Package 中的 unresolved items、completed compact view、evidence、最新失败
  和 conflicts，不再绕过 Compiler 附加 `full_plan`。
- Resume：用户输入先写 `user_input` Artifact，下一轮验证 ownership/version/hash 后才加载。

## 10. API、前端与配置

Run Trace 新增 `context_snapshots`。前端显示 purpose、Plan/Step、input limit、output reserve、
Tokenizer、provider delta、partition token、included/omitted source、Tools、Compaction 和 Conflict。

```text
LEARNLOOP_AGENT_CONTEXT_TOKENS=12000
LEARNLOOP_AGENT_CONTEXT_OUTPUT_RESERVE_TOKENS=2048
LEARNLOOP_AGENT_CONTEXT_RECENT_OBSERVATIONS=24
LEARNLOOP_AGENT_CONTEXT_SOURCE_TTL_SECONDS=86400
LEARNLOOP_AGENT_CONTEXT_DEBUG_FULL=false
```

配置校验保证 output reserve 小于 Context budget。

## 11. Failure semantics

- stale Plan/Step、missing/corrupt/cross-Run Artifact：`ContextReferenceError`；
- mandatory overflow：`ContextBudgetError`；
- optional overflow：继续执行，但 Snapshot 记录 eviction；
- expired optional source：移出本次 Context 并记录 reason；
- conflict：保留 provenance 并提示重新验证；
- Provider tokenizer 不存在：使用显式 fallback，不声称 exact；
- debug 未开启却请求 full Context：API 返回 `403`。

这些错误由 Dynamic Runtime 的统一 failure boundary 转成可解释的 terminal Run，不退回未经约束
的 Prompt 拼接。

## 12. 测试与代码阅读顺序

1. `dynamic/context.py`：Request、partition、Token packing、JIT resolution；
2. `dynamic/kernel.py`：三个 Policy 接入和 Artifact 写入；
3. `execution/store.py`：Artifact/Snapshot persistence 和 calibration；
4. `dynamic/policy.py`：所有 Model Policy 只消费 ContextPackage；
5. `tests/test_context_engine.py`：budget、integrity、dedup、conflict、expiry、privacy；
6. 前端 Agent Run 页面：Context explainability。

测试覆盖 mandatory/optional packing、evidence preservation、Artifact integrity、duplicate
compaction、conflict、expiry、tokenizer selection、metadata-only persistence、Kernel 回归和 full
Context API 权限边界。

## 13. 验收边界

本次完成 Stage 12 engineering implementation 和确定性测试。Token 实际降幅、长任务成功率、
Tool selection accuracy 和 fallback estimation 误差分布，需要 Phase 10.5 冻结 Scenario 与真实
模型运行后才能得出。当前可以审查、重放和度量 Context policy，但不伪造线上统计结论。

Stage 13 可以在这些门禁通过后，将 `memory_query` 接入受治理的跨 Session Memory。
