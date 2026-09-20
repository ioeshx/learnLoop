# v3 Stage 18：Trust 与 Agent Policy Engine

## 1. 阶段结果

Stage 18 把原来散落在 Tool allowlist、Context 过滤和 approval 中的安全判断，收敛为一个
deterministic Policy Decision Point（PDP）。Dynamic Agent 的 Tool boundary 与 Model Context
boundary 都成为 Policy Enforcement Point（PEP），每次授权都能以 metadata-only record 审计和重放。

本阶段解决的是“Agent 是否拥有执行某个动作的 authority”，不是让 LLM 自己判断是否安全。
Plan、Prompt、Memory、Skill、Bandit Policy 和 Reward 都不能绕过 deny。

## 2. 架构

```text
trusted runtime state                       untrusted/model data
Plan Step + ToolSpec + Run identity         Observation + Memory + Tool output
          │                                             │
          ├── issue CapabilityGrant                     ├── DataLabel
          └──────────────────┬──────────────────────────┘
                             ▼
                    PolicyRequest (metadata only)
                             │
                    AgentPolicyEngine / PDP
                 information-flow → grant → approval
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
        allow              deny         require_approval
          │                  │                  │
          ▼                  └──── fail closed ─┘
 ToolExecutor / ContextCompiler PEP
          │
          ├── append-only PolicyDecision audit
          ├── Run event / Context snapshot reference
          └── monotonic output label propagation
```

代码分层：

| 层 | 主要文件 | 职责 |
| --- | --- | --- |
| Contract | `backend/app/agent/policy/models.py` | Label、Subject、Grant、Request、Decision 的 strict schema |
| PDP | `backend/app/agent/policy/engine.py` | 固定顺序、无 LLM 参与的授权决策 |
| Information flow | `backend/app/agent/policy/lineage.py` | label lattice join 与 delegated scope attenuation |
| Application service | `backend/app/agent/policy/service.py` | idempotent persistence、query、dry-run simulation |
| Tool PEP | `backend/app/agent/dynamic/tools.py` | Tool 调用前授权，执行后 taint propagation |
| Model PEP | `backend/app/agent/dynamic/context.py` | Context 发送给 Provider 前授权，secret fail closed |
| Trusted grant issuer | `backend/app/agent/dynamic/kernel.py` | 从 Run/Plan/Step 生成 Subject 和稳定 Grant |
| Audit store | `backend/app/agent/execution/store.py` | append-only decision ledger 与 fingerprint unique constraint |

## 3. Trust label 与 information-flow lattice

`DataLabel` 不保存正文，只保存：

- `source/source_ref`：数据来自 user、model、tool、resource、memory、subagent 或 system；
- `trust`：`untrusted < user_asserted < verified < system`；
- `sensitivity`：`public < internal < personal < secret`；
- `integrity`：`unverified < hashed < verified`；
- `injection_signals`：instruction-like、credential request、authority claim、data exfiltration；
- `parent_label_ids`：派生结果的 lineage。

普通 transformation 使用 lattice join：取最低 trust、最高 sensitivity、最低 integrity，并合并所有
injection signals。这个规则保证 taint monotonicity：Tool 或 Model 不能因为重新表述内容就提升信任。
真正的 trust upgrade 必须由未来独立 Verifier 产生新 label，不能复用普通 transformation 路径。

`system` trust 只能由 `DataSource.SYSTEM` 声明。该校验在 Pydantic contract 层完成，因此伪造数据在
进入 PDP 前就会失败。

## 4. Capability-based authorization

`CapabilityGrant` 是 runtime authority，不是自然语言指令。它绑定：

- `subject_id`：Lead Agent 或 Child Agent identity；
- `capability`：例如 `tool:resource.search`、`model:context`；
- `resource_pattern`：Run/Plan/Step/Tool 的 scope；
- `expires_at`：可选时效；
- `max_delegation_depth`：delegation 上界；
- `issuer/source`：授权来源。

Dynamic kernel 只根据 validated Plan Step 的 allowlist 生成 exact Tool Grant。Model 输出的 tool name
仍需同时通过 registry、Step allowlist 和 PDP，避免 confused deputy。Model Context Grant 则绑定
`run/plan/step/purpose`，Planner、Decision 和 Replan 是不同 resource。

`attenuate_grant_resource()` 提供 conservative scope attenuation。Stage 20 Agent Team 会用它确保
Child Grant 是 Parent scope 的真子集；Stage 18 不把 Parent credential 或全量 Context交给 Child。

## 5. PDP 决策顺序

`AgentPolicyEngine` 以固定顺序执行：

1. information-flow hard gate：`secret` 禁止进入 Model Context；
2. exfiltration hard gate：`secret` 禁止进入 write Tool；
3. Prompt Injection gate：带 injection signal 的数据禁止触发 side effect；
4. capability match：subject、capability、resource pattern、expiry、delegation depth；
5. risk gate：high-risk 未批准返回 `require_approval`；
6. Tool approval policy：`always` 且未批准返回 `require_approval`；
7. 其余请求 `allow`。

hard gate 在 Grant 之前执行，因此“拥有 Tool Grant”不代表可以携带 secret 或 injected data 执行
side effect。`require_approval` 也不是弱 allow，PEP 会与 deny 一样停止执行。

稳定 reason code 包括 `secret_to_model`、`secret_to_tool`、
`injection_to_side_effect`、`no_capability_grant`、`grant_expired`、
`delegation_depth_exceeded` 和 approval reasons。

## 6. Tool 与 Context 集成

### 6.1 Tool boundary

`ToolExecutor` 的顺序是 registry → Step allowlist → PDP → input validation → timeout-bound invoke →
bounded output。成功和失败结果都携带 decision id/effect/reason；Tool output 与输入 label 做 join，
因此 untrusted Resource 经 Research Tool 处理后仍是 untrusted。

Tool arguments 不进入 Policy audit。调用日志继续使用原有 redaction，Policy fingerprint 只使用
subject、action、resource、label metadata、Grant metadata 和 request reference。

### 6.2 Model Context boundary

`ContextCompiler` 在 Context values 发送给 Model Provider 前调用同一 PDP：

- objective 作为 `user_asserted/personal` label；
- requested Observation 保留原始 labels；
- recalled Memory 作为 `user_asserted/personal/hashed` label；
- 任何 `secret` label 都产生持久化 deny，再以 `ContextReferenceError` fail closed；
- allow 时只把 decision id/effect/reason/version 写入 Context policy partition 和 snapshot event。

未注入 PolicyService 的 isolated compiler 仍保留直接 secret guard，作为 defense in depth；生产
runtime 总是注入中央 PolicyService。

## 7. 幂等审计与可观测性

`PolicyRequest` 生成 SHA-256 fingerprint。fingerprint 明确排除 protected value、Tool arguments、
完整 Prompt 和 credential；SQLite 对 fingerprint 建 unique constraint。Crash/retry 或并发 race 会返回
同一个 `PolicyDecision`，不会制造语义不同的重复审计记录。

审计表 `learnloop_agent_policy_decisions` 支持按 Run 查询。Agent Trace 将授权决策放在
`authorization_decisions`，与 Stage 17 的 Bandit `policy_decisions` 分开，避免把 authorization policy
和 optimization policy 混为一谈。

事件类型新增 `policy_evaluated` 和 `policy_denied`；Context snapshot event 保存其
`authorization_decision_id`。所有记录都只含 metadata 和 label id，不含 hidden Chain-of-Thought 或正文。

## 8. API、Console 与配置

- `GET /api/v1/agent/policy/decisions`：按 `run_id` 查询授权 ledger；
- `POST /api/v1/agent/policy/simulate`：不落库的 dry-run；
- `/agent-policy`：内部 metadata-only Policy Console；
- Agent Run Trace：独立显示 authorization decisions；
- `LEARNLOOP_AGENT_TRUST_POLICY_ADMIN_ENABLED`：控制 simulation mutation surface。

Simulation 使用与生产相同的 engine，但不持久化结果。普通查询不回传 Prompt、arguments 或敏感值。

## 9. Security evaluation

冻结数据集 `backend/evals/datasets/policy_v1.json` 覆盖：

- missing/wrong capability；
- secret → Model、secret → write Tool；
- injected input → side effect；
- high-risk approval；
- label monotonicity；
- audit redaction；
- replay idempotency。

门禁指标：decision accuracy、capability safety、injection block、approval gate、taint monotonicity、
audit redaction、replay idempotency 均为 1.0；`secret_exposure` 为 lower-is-better，要求 0.0。

## 10. 已知边界

- `injection_signals` 当前由 trusted adapter/fixture 提供；本阶段没有宣称实现通用 Prompt Injection
  classifier。即使未来添加 classifier，其输出也只能作为 signal，不能替代 deterministic PDP。
- Stage 18 只提供 delegated Grant attenuation primitive；通用 Child lifecycle 在 Stage 20 完成。
- 目前没有外部 MCP/A2A wire authorization、remote Policy service 或 distributed audit ledger。
- Console 是内部运维界面，未实现组织级 RBAC；admin flag 是当前部署边界。
- Policy Engine 不执行内容解密、DLP scanning 或 credential vault；secret 必须在进入 Agent runtime 时
  已被 adapter 正确标注。

## 11. 审查入口

建议按以下顺序阅读：

1. `policy/models.py`：先理解 authority 和 label contract；
2. `policy/engine.py`：确认 hard gate 的固定优先级；
3. `policy/lineage.py`：检查 lattice join 是否单调；
4. `dynamic/tools.py` 与 `dynamic/context.py`：检查两个 PEP；
5. `dynamic/kernel.py`：确认 Grant 只能来自 trusted runtime state；
6. `execution/store.py`：确认 fingerprint unique 与 metadata-only persistence；
7. `tests/test_agent_policy.py` 和 policy eval dataset：从攻击样例反向验证 invariant。

