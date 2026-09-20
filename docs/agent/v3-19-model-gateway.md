# v3 Stage 19：Model Gateway 与 Resilient Routing

## 1. 阶段结果

Stage 19 在 `ModelProvider` 上增加一个 provider-neutral Gateway。上层 `StructuredModel`、Dynamic
Agent、Research Tutor 和课程生成器不需要知道实际 Provider；它们继续提交同一个 `ModelRequest`，
Gateway 负责 capability/budget/deadline/residency preflight、deterministic routing、circuit breaker
和 compatible fallback。

当前默认部署仍只有一个 DeepSeek adapter，因此生产配置不会伪造“已有多云 fallback”。Gateway
支持多个 `ModelProfile + ModelProvider` entry，专项测试用两个独立 Fake Provider 验证真实 fallback
路径；增加第二个外部 Provider 时不需要修改 Agent kernel。

## 2. 架构

```text
StructuredModel
  │ ModelRequest + route_affinity_key
  ▼
ModelGatewayProvider
  ├─ derive ModelRequirement
  ├─ CapabilityRouter
  │    capability → context/output → residency → cost → deadline → health
  ├─ CircuitBreakerPool
  │    closed → open → half_open → closed/open
  ├─ Provider A ── retryable failure ──┐
  └─ Provider B ◄ compatible fallback ┘
          │
          ├─ ModelResponse + ModelRouteMetadata
          └─ ModelRouteRecord → SQLite → Run Event / API / Console
```

核心文件：

| 文件 | 职责 |
| --- | --- |
| `infrastructure/llm/gateway/models.py` | versioned Profile、Requirement、Route、Attempt、Health contract |
| `gateway/router.py` | hard filter 与 deterministic score |
| `gateway/circuit.py` | per-provider circuit state machine |
| `gateway/provider.py` | deadline、fallback、repair affinity、observer 与 Provider facade |
| `infrastructure/llm/models.py` | provider-neutral request/response route metadata |
| `agent/execution/store.py` | metadata-only route ledger 与 replayable events |
| `api/routes/model_gateway.py` | profiles、health、routes read API |

## 3. Profile 与 Requirement

`ModelProfile` 是 trusted deployment metadata：

- `profile_version`、`provider_id`、`model`；
- `capabilities`：JSON mode、Tool calling、long context、vision；
- `context_window`、`max_output_tokens`；
- input/output estimated cost；
- expected latency、latency class；
- data residency、priority、enabled。

`ModelRequirement` 从 `ModelRequest` 派生。结构化调用自动要求 `json_mode`；输入 Token 未显式提供时
使用与 Context fallback 一致的 conservative char estimate。Request 可以进一步声明 capability、
max estimated cost、deadline 和 residency。

Profile 价格为部署配置，不从 Model 输出或远端响应动态注入，避免 route manipulation。API key 从不
进入 Profile、RouteRecord 或前端。

## 4. Routing pipeline

Router 先 hard filter，再排序：

1. profile enabled；
2. required capabilities 是 profile capabilities 的子集；
3. estimated input + reserved output 不超过 context window；
4. output reservation 不超过 Provider 上限；
5. data residency exact match；
6. worst-case estimated cost 不超过 ceiling；
7. expected latency 不超过 deadline；
8. circuit 当前允许调用。

只对通过全部 hard filter 的候选计算 score。排序 tuple 为：

```text
repair route affinity → higher deployment priority → lower expected latency
→ lower estimated cost → provider_id stable tie-break
```

如果没有候选，Gateway 在调用任何 Provider 前失败。空 `RoutePlan` 仍被序列化，因此每个 Provider
的 `missing_capabilities`、`context_window_exceeded`、`estimated_cost_exceeded`、
`data_residency_mismatch`、`deadline_preflight_failed` 或 `circuit_unavailable` 原因可以审计。

## 5. Fallback semantics

Fallback 只发生在：

- Provider 抛出 `ModelProviderError(retryable=True)`，例如最终 429/5xx/transport failure；
- Gateway 的总 deadline timeout。

authentication、authorization、invalid request 和 invalid response envelope 等 non-retryable error
立即返回，不会切换 Provider。所有 fallback candidate 都已通过同一份 `ModelRequirement`，因此不会
为了恢复可用性降低 JSON/security/context/residency 能力。

DeepSeek adapter 保留自身短周期 retry；只有 adapter 的 retry budget 耗尽后，Gateway 才进行跨
Provider fallback。这使单 Provider retry 与跨 Provider failover 的责任边界明确。

## 6. Structured repair affinity

`StructuredModel` 为一次 logical generation 生成稳定 `route_affinity_key`，first attempt 与唯一一次
JSON repair 使用同一个 key。Gateway 记住成功 Provider；repair 优先回到该 Provider，避免两个模型
在同一结构化输出中出现 semantic drift。

只有 affinity Provider 因 circuit unavailable 或不再满足 hard requirement 时，repair 才允许重新
路由。Affinity 不是 capability bypass，它只影响已经通过 filter 的候选排序。

## 7. Circuit breaker

每个 Provider 有独立 state：

```text
closed --N retryable failures--> open
open --cooldown elapsed--------> half_open (single probe)
half_open --success------------> closed
half_open --retryable failure--> open
```

open cooldown 内 Provider 被 Router 排除；half-open 同时只允许一个 probe。Non-retryable error 不增加
availability failure counter，也不触发 fallback。每个 RouteAttempt 保存 circuit state before/after；
state transition 产生 `model_circuit_changed` Run event。

当前 circuit state 是进程内 health state，route/audit 是 durable。进程重启后 breaker 从 closed 开始，
这是单实例本地部署的明确边界；多实例共享 breaker 需要外部一致性存储。

## 8. Deadline、cost 与 usage

- cost 使用 reserved output 计算 worst-case estimate，而不是成功响应后的实际 output；
- deadline 是整个 Gateway request 的 wall-clock budget，fallback 共享剩余时间；
- Provider 的 expected latency 超过 deadline 时在 preflight 阶段拒绝；
- Gateway `TokenUsageTracker` 只记录最终成功响应的 provider-reported usage；
- estimated cost 与 actual billing 明确分开，route ledger 不把估算描述为账单。

Dynamic Agent 自身仍有 Run Token/deadline ledger。Gateway preflight 是更靠近 Provider 的第二层防线，
不能放宽 Agent budget。

## 9. Durable observability

`learnloop_model_routes` 保存完整 metadata-only `ModelRouteRecord`：requirement、selected provider/model、
attempts、retryable flag、duration、rejection reasons、fallback count 和 cost estimate。不保存 Prompt、
response、credential 或 hidden reasoning。

Run events：

- `model_routed`：首次候选成功；
- `model_fallback`：一个或多个 retryable attempt 后成功；
- `model_route_failed`：preflight 或执行失败；
- `model_circuit_changed`：closed/open/half-open transition。

API 与 UI：

- `GET /api/v1/agent/model-gateway/profiles`；
- `GET /api/v1/agent/model-gateway/health`；
- `GET /api/v1/agent/model-gateway/routes?run_id=...`；
- `/model-gateway` 展示 profiles、live health、route/fallback ledger；
- Agent Run Trace 显示该 Run 的 route decisions。

## 10. 配置

配置前缀为 `LEARNLOOP_LLM_GATEWAY_`，包括 enabled、context/output limits、estimated input/output
cost、expected latency、residency、failure threshold、recovery、deadline 和 optional max estimated
cost。默认 cost 为 0，表示运维尚未填写价格，而不是 Provider 免费；需要 cost gate 时必须配置真实
部署价格和 ceiling。

现有 `LEARNLOOP_LLM_PROVIDER=deepseek` 保持兼容。关闭 Gateway 时直接使用原 DeepSeek adapter，
便于 ablation 和故障隔离。

## 11. 测试与冻结评测

`test_model_gateway.py` 覆盖：

- capability/cost/deadline/residency hard filter；
- 429 retryable fallback；
- authentication error 不 fallback；
- repair affinity；
- closed/open/half-open/closed；
- preflight 不调用 Provider；
- route persistence、Run event、profiles/health/routes API。

冻结集 `model_gateway_v1.json` 的门禁包括 route accuracy、capability safety、retryable recovery、
non-retryable fallback rate、preflight block、repair affinity 和 circuit recovery。Safety/capability 指标
必须为 1.0，non-retryable fallback rate 必须为 0.0。

## 12. 已知边界

- 默认配置只有一个外部 Provider；跨云 fallback 需要部署方显式增加 adapter/profile/credential。
- 当前没有自动发现 Provider、动态价格抓取或跨实例 distributed circuit breaker。
- profile data residency 是部署声明，不是网络层 geo-attestation。
- Tool-calling/vision contract 已预留，但当前 LearnLoop StructuredModel 只要求 JSON mode。
- Route Console 是内部只读界面；修改 profile 仍通过受控部署配置完成。

## 13. 审查入口

建议先看 `gateway/router.py` 的 hard filters，再看 `gateway/provider.py` 的 retry classification 和
deadline sharing，随后检查 `gateway/circuit.py` 的 single probe，最后用 `test_model_gateway.py` 与
route ledger 验证失败路径没有 capability downgrade。

