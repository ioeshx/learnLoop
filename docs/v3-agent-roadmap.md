# LearnLoop v3 Agent 路线图

## 1. 版本主题

v3 的主题是 **Trusted, Resilient, Collaborative Agent Runtime**。

v2 回答“Agent 能否自主规划、使用工具、记忆、研究、委派和学习”；v3 回答：

- 自主决策是否统一受 Policy 控制；
- 多种 Model/Provider 失败时是否可解释地降级；
- 多个专业 Agent 是否能在 scope/budget 下协作；
- 面对随机性和故障时是否仍然可靠。

## 2. Stage 18：Trust 与 Agent Policy Engine

### 目标

建立统一 Policy Decision Point（PDP），由 Tool Executor、Context、Delegation 和未来协议适配器
共同使用；调用点作为 Policy Enforcement Point（PEP）。

### 交付

1. `DataLabel`：trust、sensitivity、source、integrity 和 injection signals；
2. `CapabilityGrant`：subject、capability、resource scope、expiry 和 delegation depth；
3. `PolicyRequest/PolicyDecision`：allow/deny/require_approval、稳定 reason code；
4. deterministic `AgentPolicyEngine`；
5. Tool execution 前 authorization，结果后 taint propagation；
6. append-only audit records 与 Trace 事件；
7. dry-run policy simulation API 和内部 Policy Console；
8. Prompt Injection、secret exfiltration、trust escalation、confused deputy 冻结评测。

### Invariants

- data trust 只能保持或下降，不能由模型输出自行提升；
- `secret` 不能进入 Model Context 或普通 Tool result；
- high-risk/write Tool 至少需要显式 Grant，必要时还需 approval；
- Child Agent 的 Grant 必须是 Parent Grant 的真子集；
- deny 决策不能被 Prompt、Skill、Policy Bandit 或 Reward 覆盖。

## 3. Stage 19：Model Gateway 与 Resilient Routing

### 目标

把单 Provider 调用升级为 capability-aware、budget-aware、可观测的 Model Gateway。

### 交付

1. `ModelProfile`：JSON mode、Tool calling、context window、cost、latency class；
2. `ModelRequirement`：调用所需能力、最大成本和延迟；
3. deterministic route score 与 route explanation；
4. per-provider circuit breaker：closed/open/half-open；
5. 仅对 retryable failure 执行跨 Provider fallback；
6. deadline 与 Token/estimated-cost preflight；
7. route/fallback/circuit events 和 API；
8. capability mismatch、429/5xx、timeout、invalid JSON、budget exhaustion 测试。

### Invariants

- non-retryable authentication/validation error 不得静默切换 Provider；
- fallback Provider 必须满足相同 output/security capability；
- route 不得突破 Run budget；
- repair call 与原调用保持同一 route，除非原 Provider circuit 已 open；
- Model 输入和 route event 默认不记录敏感正文。

## 4. Stage 20：General Agent Team Runtime

### 目标

把专用 Researcher Delegation 抽象成通用、可验证的内部 Agent Team runtime。

### 交付

1. `AgentCard/RoleSpec`：capabilities、input/output contract、Tool scope、risk；
2. `TeamTask` lifecycle：submitted/running/input_required/completed/failed/cancelled；
3. `TeamArtifact`：typed Parts、hash、provenance、DataLabel；
4. Role Registry 与 trusted adapter；
5. bounded parallel fan-out/fan-in；
6. hierarchical Token/deadline/child-count budget；
7. parent cancellation、duplicate suppression 和 partial-result policy；
8. Lead Verifier 在 Artifact 进入 Context 前复验；
9. Researcher adapter，并为 Evaluator/Curriculum analysis 预留接口；
10. Team Trace 与内部页面。

### Invariants

- Agent Card 只是 capability declaration，不自动授予权限；
- Child 不继承完整 Parent Context、Memory 或 credentials；
- Artifact 必须经过 hash/provenance/Policy/Verifier 才能进入 Lead Context；
- parallelism、总 Token 和 deadline 都有硬上限；
- 不支持递归无界 delegation。

内部数据模型借鉴 A2A 的 Agent Card、Task、Message/Part 和 Artifact，但 v3 不宣称实现 A2A
HTTP binding。

## 5. Stage 21：Agent Reliability Lab

### 目标

从单次确定性 fixture 升级为支持 stochastic trials、fault injection 和 worst-case slice 的评测。

### 交付

1. versioned Scenario 与 Trial manifest；
2. deterministic seed 和 environment snapshot；
3. fault injectors：timeout、transient Tool、malformed Model、stale Artifact、goal shift、injection；
4. final-state、trajectory invariant、Safety 和 cost grader；
5. `pass@k`、`pass^k`、recovery rate、redundancy、cost tail；
6. fixed/dynamic/policy/model route ablation；
7. JSON report、API 和内部 Reliability dashboard；
8. CI fast gate 与手动 full stochastic suite 分层。

### Invariants

- success 以 final state 和 required evidence 为准，不要求固定 trajectory；
- Safety violation 使该 Trial 失败，不被 task score 抵消；
- fault schedule 与 seed 必须可重放；
- fixture 结果不能被描述为真实生产效果。

## 6. 依赖顺序

```text
Stage 18 Trust/Policy
      │
      ├──────────────► Stage 19 Model Gateway
      │                         │
      └──────────────► Stage 20 Agent Team
                                │
                    Stage 21 Reliability Lab
                    evaluates all previous stages
```

Stage 19 和 20 都依赖 Stage 18 的 Policy contract；Stage 21 最后冻结跨阶段门禁。

## 7. v3 退出门禁

- Policy deny/approval/trust lineage 可从 Run Trace 重建；
- Model Gateway 在故障下可控 fallback，无 capability 或预算降级；
- 至少两个 Role adapter 通过 Team contract，且 bounded parallel 有真实 wall-clock/Token 报告；
- `pass@k/pass^k` 与六类 fault injection 可重复运行；
- Stage 18–21 的 Safety rate 为 1.0，unsafe 样本不进入 Skill/Reward/training export；
- 后端全量测试、Mypy、Ruff、前端测试/typecheck/lint/build 通过；
- 工作区干净，每个功能组有语义明确的 Git commit；
- 外部 MCP/A2A、sandbox、multimodal 和真实训练仍保持未启用，除非其独立门禁完成。

## 8. 推荐提交序列

1. `docs(v3): define trusted resilient agent roadmap`
2. `feat(policy): add trust labels and agent policy engine`
3. `feat(policy-api): expose policy audit and simulation console`
4. `test(policy): add taint and capability security gates`
5. `feat(model-gateway): add capability routing and circuit breaker`
6. `test(model-gateway): add fallback and budget fault coverage`
7. `feat(agent-team): add role registry task artifacts and bounded fanout`
8. `feat(agent-team-ui): expose team lifecycle and artifacts`
9. `test(agent-team): add scope budget cancellation and fanin gates`
10. `feat(reliability): add stochastic trials fault injection and pass-k reports`
11. `test(v3): freeze cross-stage reliability and security gates`
12. `docs(v3): complete architecture operation and review guide`
