# v3 Stage 20：General Agent Team Runtime

## 1. 阶段结果

Stage 20 将 Stage 15 的专用 Researcher delegation 扩展为通用、policy-bounded Agent Team
runtime。Lead Agent 可以提交一个 typed Task DAG，Scheduler 在 child count、parallelism、Token 和
deadline 硬上限内执行 trusted Role adapter，并只允许 verified Team Artifact 回流。

首批 Role：

- `researcher`：适配现有本地 Agentic RAG / cited Researcher，保留原有 child Run lifecycle；
- `evaluator`：deterministic rubric/evidence inventory，只提供 advisory Artifact，不能覆盖 Lead
  deterministic Verifier。

现有 `delegate.research` Tool 已切到 Team compatibility facade，因此 Dynamic Agent 的真实委派路径会
产生 TeamTask、PolicyDecision 和 TeamArtifact，而不是仅提供一套未接入的旁路 API。

## 2. 架构

```text
Lead Agent validated Plan Step
        │
        ▼
TeamRunRequest (typed DAG + hierarchical budget)
        │
        ├─ DAG validation / reservation / duplicate fingerprint
        ├─ RoleRegistry (trusted adapter code)
        └─ Policy DELEGATE decision
                │ attenuated child Grants
                ▼
       bounded fan-out Scheduler
       ┌────────┼────────┐
 Researcher  Evaluator  future Role
       └────────┼────────┘
                ▼
         ArtifactDraft (untrusted)
                │
      hash + label join + Lead Verifier
                │
      Policy ARTIFACT_IMPORT decision
                ▼
        verified TeamArtifact
                │
      evidence_ref-only DAG fan-in
                ▼
        Lead Tool result / Context
```

主要模块：

| 文件 | 职责 |
| --- | --- |
| `agent/team/models.py` | AgentCard、Task DAG、Budget、Part、Artifact、Result contract |
| `agent/team/registry.py` | trusted Role adapter registry |
| `agent/team/service.py` | DAG scheduler、Policy、budget、cancellation、duplicate、fan-in |
| `agent/team/verifier.py` | content hash、taint join、revalidation |
| `agent/team/adapters.py` | Researcher compatibility 与 deterministic Evaluator |
| `agent/execution/store.py` | durable Task/Artifact tables 与 Run events |
| `api/routes/agent_team.py` | Role、Task、Artifact query 与 admin execution |

## 3. Agent Card 与 authority 分离

`AgentCard` 声明 Role version、capabilities、input/output contract、allowed Tool names、risk 和
read-only 属性。Card 来自本地 trusted registry，但它只是 declaration，不是 Grant。

每个 TeamTask 在执行前由中央 PDP 评估 `PolicyAction.DELEGATE`：

- Subject 是 Lead Agent，而不是模型生成的 role name；
- capability 为 `agent:delegate:{role}`；
- resource 绑定 `run/step/team-task/role`；
- input Part 的 labels 一并参与 information-flow gate；
- `secret` input 返回 `secret_to_subagent`，adapter 不会被调用。

授权后才生成 Child Tool Grants。Grant subject 是独立 TeamTask identity；resource scope 从
`run:{parent}:team-task:*` attenuation 为 exact Task/Tool resource。Child 不继承完整 Parent Context、
Memory、credentials 或 Tool allowlist。

## 4. Task DAG 与 bounded parallelism

`TeamRunRequest` 在 Pydantic contract 层验证：

- task key 唯一；
- dependency 必须存在；
- 禁止 self-dependency；
- DAG 必须 acyclic；
- Task Token reservations 总和不超过 Team budget；
- task count 不超过 manifest max children。

Service 再与 deployment 上限以及 Lead DynamicAgentState 的剩余 Token/deadline 相交。已有 Task 的
reservation 也计入 parent pool，防止通过多次 batch 绕过 hierarchical budget。

Scheduler 以 ready wave 执行 DAG。`asyncio.Semaphore` 限制 active adapter 数量；同一 wave 使用排序后
的 key 形成 deterministic fan-in。依赖失败时，下游 Task 标为 `dependency_failed`。策略：

- `fail_fast`：任一失败后取消其余 pending Tasks；
- `verified_partial`：无依赖于失败分支的 Tasks 仍可继续，最终只返回 verified Artifacts。

## 5. Durable lifecycle 与 duplicate suppression

Task lifecycle：

```text
submitted → running → completed
                    ↘ failed
submitted/running ──→ cancelled
```

每个 Task fingerprint 包含 parent、Plan Step、role、objective、Parts、dependencies 和 allocation。
SQLite 对 `(parent_run_id, fingerprint)` 建 unique constraint。Crash/retry 或并发重复提交会读取原 Task；
已完成 Task 的 Artifact 通过 hash revalidation 后直接复用，不重复调用 adapter。

父 Run cancel 时，Scheduler 取消 active adapter coroutine，并将 pending Tasks 标为 cancelled。Team
deadline 是 batch 共享的 wall-clock 上限，不会为每个 Child 重新计时。

## 6. Verified Artifact boundary

Role adapter 只能返回 `ArtifactDraft`。Lead-side `TeamArtifactVerifier`：

1. 检查 Task role 与 trusted AgentCard 一致；
2. 检查 used tokens 不超过 reservation；
3. 对所有 Part labels 做 monotonic join；
4. 对 task id、role、Parts 和 metadata 计算 canonical SHA-256；
5. 标记 verifier version；
6. 再调用 PDP 的 `PolicyAction.ARTIFACT_IMPORT`。

`secret` Artifact 被 `secret_artifact_import` 拒绝。只有 hash verified 且 Policy allow 的 Artifact 才落库。
Policy decision id 独立存放，不进入 content hash，避免审计 metadata 改变 Artifact identity。

下游 Task 不接收上游私有 Context，只得到 `evidence_ref` Part：artifact id、SHA-256、role 与 joined
labels。这样 fan-in 可验证 provenance，同时避免复制 Child scratchpad 或 hidden reasoning。

## 7. Researcher 与 Evaluator

### Researcher adapter

Researcher adapter 调用 Stage 15 `DelegationService`，所以继续使用 trusted goal/node scope、local-only
retrieval、citation graph verifier、child Run、deadline/cancel 和 compressed result。其
`DelegationResult` 作为 `personal/untrusted/hashed` JSON Part 进入 Team verifier。

`delegate.research` Tool 接收的是 `AgentTeamService` facade；失败的原 Delegation status 仍由 Tool
Executor 映射为 typed ToolError，保持旧 contract。

### Evaluator adapter

Evaluator 只检查 answer 是否存在、rubric inventory 和 evidence ids，输出
`verifier_override_allowed=false`。它不使用 write Tool、不产生最终成绩，也不能修改 deterministic
Verifier、Reward hard gate 或 mastery state。

## 8. Persistence、events、API 与 Console

SQLite：

- `learnloop_team_tasks`：唯一 fingerprint、status、完整 strict Task JSON；
- `learnloop_team_artifacts`：task unique、role、sha256、Artifact JSON。

Run events：`team_task_submitted`、`team_task_started`、`team_task_failed`、
`team_task_cancelled`、`team_artifact_verified`、`team_completed`。

API：

- `GET /api/v1/agent/team/roles`；
- `GET /api/v1/agent/team/tasks?parent_run_id=...`；
- `GET /api/v1/agent/team/artifacts?parent_run_id=...`；
- `POST /api/v1/agent/team/execute`，受 admin flag 保护。

`/agent-team` 展示 Registry、Task DAG、Token usage 与 Artifact provenance；Run Trace 合并显示该 Run 的
Tasks 和 Artifact SHA。页面不展示 Child private Context。

## 9. 配置

`LEARNLOOP_AGENT_TEAM_*` 控制 enabled、admin execution、max parallel children、max children、max total
tokens 和 deadline。Researcher 单任务 allocation 继续复用 Stage 15 delegation Token setting。

关闭 Team runtime 时，`delegate.research` 回退到原 DelegationService，便于 ablation；不会回退到无
scope/budget 的自由 Subagent。

## 10. 测试与冻结评测

专项测试覆盖：

- 两个并行 root Tasks 的真实 concurrency bound；
- verified Artifact references 进入依赖 Evaluator；
- Child Tool Grant scope attenuation；
- secret delegation 在 adapter 前 deny 且留有 audit；
- duplicate manifest 复用原 Artifact；
- parent cancellation 传播；
- cycle 与 budget overcommit contract rejection；
- Role/Task/Artifact API；
- Stage 15 原有 isolation、citation、deadline、cancel 和 Tool mapping 回归。

冻结集指标包括 task success、scope safety、budget safety、parallel bound、cancel propagation、duplicate
prevention、Artifact verification 和 secret delegation rate。所有 safety rate 要求 1.0，secret
delegation rate 要求 0.0。

## 11. 与 A2A 的关系和边界

内部模型借鉴 A2A 的 Agent Card、Task、Part、Artifact 和 lifecycle，但 Stage 20 不实现 A2A HTTP
binding、Agent discovery、remote authentication 或跨组织 trust。所有 Role adapter 都是同进程 trusted
code，Artifact transport 是本地 SQLite。

当前不支持递归 Team：Child Grant depth 固定为 1，Role adapter 不能再次调用 TeamService。未来接入
remote A2A 前必须增加 Agent Card signature、network allowlist、auth、task polling/push hardening 和
跨边界 DLP。

## 12. 审查入口

建议依次检查 `models.py` 的 DAG/budget invariants、`service.py` 的 ready wave 和 Policy 两个边界、
`verifier.py` 的 hash/label join、`adapters.py` 的 private Context 隔离，最后从
`test_agent_team.py` 的 secret/cancel/duplicate 场景反向验证。

