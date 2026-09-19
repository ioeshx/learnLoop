# LearnLoop v3 Agent 能力缺口分析

## 1. 文档目的

本文以 `v3` 分支代码为事实基线，回答两个问题：

1. v2 已经具备哪些高级 Agent 能力；
2. 哪些技术、部件和产品特性仍然缺失，以及哪些应进入 v3。

评估日期为 2026-09-19。协议与生态判断参考当时的公开规范；仓库能力判断只以当前代码、
测试和文档为依据。

## 2. v2 已完成的 Agent 能力

| 能力 | 代码证据 | 状态 |
| --- | --- | --- |
| Dynamic Agent Loop | `agent/dynamic/kernel.py` | 已完成 |
| typed Plan / Replan / Verifier | `agent/dynamic/models.py`、`verifier.py` | 已完成 |
| Tool Registry / allowlist / approval / idempotency | `agent/dynamic/tools.py` | 已完成 |
| Context budgeting / JIT Artifact / compaction | `agent/dynamic/context.py` | 已完成 |
| Working / Episodic / Semantic / Procedural Memory | `agent/memory/`、`agent/experience/` | 已完成 |
| Agentic RAG / citation verification | `agent/research/` | 已完成 |
| Subagent-as-Tool | `agent/delegation/` | 部分完成：只有串行 Researcher |
| Reflection / Skill Library | `agent/experience/` | 已完成 |
| Contextual Bandit / Reward / OPE | `agent/optimization/` | 控制面完成，真实训练未放行 |
| Checkpoint / Interrupt / SSE / replay | `agent/execution/` | 已完成 |
| deterministic evaluation fixtures | `backend/evals/` | 已完成基础版 |

因此 v3 的重点不是继续堆叠 Prompt，而是补齐跨模块的一致治理、韧性、协作和评测能力。

## 3. 缺口矩阵

### 3.1 进入 v3 的能力

| 缺口 | 当前问题 | v3 决策 | 优先级 |
| --- | --- | --- | --- |
| Central Policy Decision Point | Tool、Memory、RAG 各自有规则，但没有统一 Policy decision、reason code 和 audit record | 实现 Trust/Policy Engine | P0 |
| End-to-end taint propagation | RAG 有 `untrusted`，但普通 Tool output、Context 和 Delegation Artifact 没有统一 label | 实现 typed DataLabel 与 non-escalation | P0 |
| Multi-model routing | 只有单一 DeepSeek Provider；没有 capability/cost/latency route、fallback 或 circuit breaker | 实现 Model Gateway | P0 |
| General Agent Team | 只有 Researcher role、串行调用；不能声明 Role capability 或运行 bounded fan-out | 实现 Role Registry、Task DAG、Artifact handoff | P1 |
| Stochastic reliability evaluation | 有单次 fixture gate，但没有 `pass@k`、`pass^k`、fault injection 和 worst-case slice | 实现 Reliability Lab | P1 |
| Unified decision observability | Trace 有事件，但 Policy、Router、Team、fault 的共同 correlation schema 不存在 | 随四阶段共同实现 | P1 |

### 3.2 当前只部分具备的能力

| 技术 | 已有部分 | 仍缺少 |
| --- | --- | --- |
| Agent security | allowlist、approval、RAG injection filter、Memory trust | centralized PDP/PEP、taint lineage、policy simulation、decision audit |
| Multi-agent | parent/child Run、预算、取消、Researcher verification | role discovery、通用 Task/Artifact、fan-out/fan-in、per-role eval |
| Agent learning | Bandit、Reward、SFT/Preference export | 真实 longitudinal dataset、model checkpoint registry、trainer integration |
| Observability | local event/model/tool trace | standard span vocabulary、cross-agent correlation、external OTLP exporter |
| Uncertainty | insufficient-evidence state、Verifier | calibrated confidence、selective prediction、abstention calibration |
| Model resilience | provider retry | cross-provider fallback、health state、circuit breaker、route rationale |

### 3.3 明确缺失但不在本次 v3 实现范围

| 能力 | 不立即实现的原因 | 进入条件 |
| --- | --- | --- |
| Remote MCP client/server | 最新 MCP 已包含 stateless core、Tasks、MRTR 和授权强化；自制不完整 wire protocol 风险高 | 选择官方 SDK、凭证隔离、remote server allowlist |
| External A2A wire protocol | 需要 HTTPS identity、signed Agent Card 和跨服务部署 | 出现真实远端 Agent 合作方 |
| Code execution sandbox | API 进程内执行代码不可接受 | 独立容器/VM、网络禁用、CPU/内存/文件限制 |
| Computer Use / browser Agent | 需要 GUI sandbox、视觉模型和高风险操作审批 | 有可复现环境与安全评测集 |
| Multimodal Tutor | 当前资料和 Context contract 以文本为主 | OCR/ASR/vision provenance 与引用定位完成 |
| External OpenTelemetry exporter | 单机本地优先暂不需要 collector | 多服务部署或远端 MCP/A2A 接入 |
| Knowledge Graph RAG | 当前关系表和 local RAG 未出现可测瓶颈 | 多跳图查询显著优于现有检索 |
| Actual SFT / Preference / RL training | 缺少足量脱敏 longitudinal trajectories | Stage 17 数据门禁全部满足 |

MCP 2026-07-28 已转向 stateless core，并加入 Tasks、Multi Round-Trip Requests、cache hints、
authorization hardening 和 Trace Context；因此未来接入必须面向该版本，而不是实现旧的有状态
握手模型：<https://blog.modelcontextprotocol.io/posts/2026-07-28/>。

A2A 将 Agent Card、stateful Task、Message、Artifact、streaming 和 authentication discovery
标准化；v3 的内部 Team contract 会借用这些稳定概念，但不宣称 wire-compatible：
<https://a2a-protocol.org/latest/specification>。

OpenTelemetry 已定义 `invoke_agent`、`execute_tool`、conversation、agent version 等 GenAI
语义字段；v3 先保证本地事件可映射到这些字段，再决定是否引入 exporter：
<https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/>。

## 4. 为什么选择四个 v3 主题

### 4.1 Trust/Policy Engine 是其他能力的前置条件

动态 Tool、外部 Agent 或模型 fallback 都会扩大输入来源。若没有统一 trust label 和 Policy
decision，MCP、A2A 或更多 Subagent 只会增加 confused-deputy 和 indirect prompt injection 风险。

### 4.2 Model Gateway 先于更多模型功能

当前 `StructuredModel` 绑定一个 Provider。直接增加第二个 Provider 会把 fallback 分散到每个
调用点，无法解释“为什么选这个模型”，也无法阻止失败 Provider 被持续调用。

### 4.3 Agent Team 必须建立在 capability 和 budget 上

现有 Researcher 证明了 parent/child、取消和 hierarchical budget。v3 应把这些 invariant
抽象为通用 Role/Task/Artifact，而不是复制多个专用 DelegationService。

### 4.4 Reliability Lab 是所有新增自治能力的退出门禁

Agent 是随机系统。单次成功只能证明 `pass@k` 的上界，不能证明重复运行可靠。v3 必须同时
报告：

```text
pass@k = k 次中至少一次成功
pass^k = k 次全部成功
```

并在 timeout、Tool transient failure、malformed output、stale Artifact、goal shift 和 injection
场景下检查 final state、Safety、成本与恢复路径。

## 5. v3 成功定义

v3 完成不等于“类名存在”。必须有以下可验证证据：

- 所有敏感 Tool action 都产生可解释、可重放的 PolicyDecision；
- untrusted/secret data 不能在传播中提升 trust 或扩大 capability；
- Model route 具有 capability match、预算、fallback 和 circuit breaker 测试；
- Team Task 的 Role、scope、budget、Artifact 和 lifecycle 都可审计；
- Reliability suite 能计算 `pass@k/pass^k` 并注入可重复故障；
- API/前端只展示脱敏 decision metadata，不展示 hidden reasoning 或 credential；
- 每个阶段有冻结评测、架构文档、回滚路径和独立 Git commit。
