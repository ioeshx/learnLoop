# LearnLoop v2 Stage 13：Agent Memory 实现说明

本文记录 Stage 13 的实际实现，重点解释 Agent 如何跨 Session 形成、治理、召回和遗忘长期
Memory。实现目标不是“保存更多文本”，而是建立一条可审计的 Trust Boundary：只有来源、时效、
冲突和审批状态都满足策略的记录，才可能进入下一次 Agent Context。

## 1. 交付结果

Stage 13 完成了四部分闭环：

1. `MemoryRecord / MemoryEvidence / MemoryRevision` 聚合、Repository、SQLite migration；
2. lifecycle-bound candidate extraction 与 deterministic governance pipeline；
3. scoped retrieval、reranking、abstention 和 Stage 12 Context partition 接入；
4. 搜索/来源/审批/更正/停用/删除 API、治理页面和 LongMemEval-style 冻结评测集。

整体数据流如下：

```text
Verified Run completed / explicit Handoff / explicit user correction
                               │
                               ▼
                       MemoryCandidate
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
      deterministic governance              rejected audit
 PII → trust → dedup → conflict → approval      record
              │
       candidate / active
              │
              ▼
 metadata scope filter → lexical recall → deterministic rerank → threshold
              │                                                   │
              ▼                                                   ▼
  Context.memory + Evidence                              empty collection
```

## 2. 四类 Memory 的边界

| 类型 | LearnLoop 中的含义 | 是否长期持久化 |
|---|---|---|
| Working | 当前 Run 的 Plan、Observation、Interrupt、未解决事项 | 否；属于 durable Agent state |
| Episodic | 已验证的某次学习 Session 经历和结果 | 是；可信 Run 完成后可自动激活 |
| Semantic | 用户偏好、稳定事实和学习画像摘要 | 是；高影响，必须审批 |
| Procedural | Agent 可复用的方法和策略 | 是；高影响，必须审批 |

`Working Memory` 已由 Stage 11/12 的 checkpoint、Artifact 和 Context Engine 管理。如果再复制到
长期表，会同时制造 stale state 和恢复歧义，因此 governance pipeline 明确拒绝将其持久化。

掌握度、复习计划和客观作答仍由原有 Domain Model 决定。Memory 的
`authoritative_domain_state=false` 表明它只能帮助 Agent 回忆与个性化，不能覆盖
`MasterySnapshot`、`ReviewSchedule` 或 `ExerciseAttempt`。

## 3. 聚合模型与时间语义

### MemoryRecord

`MemoryRecord` 是当前 materialized state，包含：

- `kind/content/attributes`：可读文本与结构化属性；
- `confidence/importance`：写入质量和检索排序信号；
- `user_id/goal_id/knowledge_node_id`：由宽到窄的作用域；
- `memory_key`：同一逻辑偏好或事实跨版本的 stable identity；
- `fingerprint`：`kind + key + normalized content` 的 SHA-256，用于 exact dedup；
- `valid_from/expires_at`：bi-temporal-like 有效期；
- `supersedes_id`：新记录对旧记录的显式替代关系；
- `candidate/active/rejected/expired`：治理状态机；
- `trust/sensitivity/requires_approval`：可信边界元数据。

`memory_key` 与 `fingerprint` 分离非常关键。前者用来判断“同一个偏好发生了更新”，后者用来判断
“完全相同的候选被重复提交”。只使用 embedding similarity 无法可靠区分这两种情况。

### MemoryEvidence

Evidence 是 append-only provenance，保存 source type/id、受限 excerpt、trust、observed time 和
可选 Run/Session/Attempt 引用。一次 exact dedup 不会创建第二条 MemoryRecord，而是为现有记录
追加 Evidence，并合并较高的 confidence/importance。

### MemoryRevision

Revision 记录初次提取、审批、拒绝、停用和更正的 actor/reason/content。API 返回完整来源和
Revision chain，前端可以解释“系统为什么记住”和“谁改变了状态”。

## 4. 持久化架构与级联删除

Memory 存入业务数据库 `learnloop.db`，而不是 `checkpoints.db`：

- checkpoint 负责同一 Run 的 crash recovery；
- Memory 是跨 Run、可搜索、可更正、受用户数据生命周期约束的业务数据；
- `SqlAlchemyUnitOfWork` 使状态变化、Evidence 和 Revision 在一个 transaction 中提交。

Migration `0007_agent_memory` 创建 `memory_records`、`memory_evidence` 和
`memory_revisions`。索引覆盖 user/status/kind、user/key/status、goal/node/status 和 exact
fingerprint。`memory_records.user_id` 使用 `ON DELETE CASCADE`，Evidence/Revision 又对 Record
级联，因此删除用户时不会留下正文、来源或索引孤儿。集成测试直接删除 User 并验证聚合不可再读。

## 5. 候选提取边界

动态 Agent 只在 Verifier 确认整个 Plan 完成时调用 `capture_run_outcome()`，生成
`Episodic Memory`。Run 中每一步的 Tool result、网页内容、模型输出和用户临时回答仍留在 Artifact
和 Trace，不会边执行边永久化。

当前自动提取刻意只覆盖 verified Episode：objective、completed Step IDs 和可选 final summary。
它没有让另一个 LLM 从任意 Tool 文本推断用户画像，也没有从单条成功轨迹生成 Procedural
Memory。这是 Stage 13 的安全基线；未来若增加 model-based extractor，其输出仍只能是
`MemoryCandidate`，不能绕过 `MemoryService.ingest()`。

显式用户更正通过 API 进入 `explicit_user` lifecycle boundary，并带 `user_asserted` trust。它仍需
审批，因为“用户输入”证明来源明确，但不等于允许 Agent 静默修改高影响画像。

## 6. Governance pipeline

`MemoryService` 是所有 durable Memory mutation 的 Policy Enforcement Point，固定顺序为：

```text
candidate extraction
→ PII / sensitivity classification
→ source trust validation
→ exact dedup and Evidence merge
→ logical-key conflict / supersession
→ high-impact approval
→ atomic persistence
```

关键规则：

- confidence `< 0.65`：保存为 `rejected` 审计记录，不进入检索；
- email、手机号、身份证样式或 secret/token-like 内容：阻断长期化；
- `untrusted` Tool/资料内容：不能成为 Semantic/Procedural Memory；
- Semantic、Procedural、profile change 和 `user_asserted`：保持 `candidate` 等待审批；
- confidence `>= 0.80` 的 verified Episodic candidate：可自动成为 `active`；
- exact duplicate：追加 Evidence，不制造平行记录；
- 同 `memory_key` 的新值：设置 `supersedes_id`；审批前旧 Active 仍有效；
- 新值批准后：同一 transaction 将旧值转为 `expired`、新值转为 `active`。

旧值在新候选“提出”时不能立刻失效，否则恶意或错误候选可以通过制造冲突实现 denial of
memory。只有审批完成才执行 supersession。

## 7. 检索与 Reranking

检索采用 two-stage deterministic pipeline：

1. 仅查询 `active`，并先按 user、goal、knowledge node、kind、expiry 做 metadata filter；
2. 对 query 和 Memory 的 content/key/attributes 做 lexical recall；
3. 按 relevance、confidence、importance、trust、recency 和 scope specificity 加权重排；
4. 低于 `minimum_score` 的结果不返回。

中文使用 CJK character bigram，英文与标识符使用 word token。单个常见汉字（例如“的”）不会
制造相关性。最重要的 no-answer rule 是：`lexical relevance == 0` 时直接跳过，质量先验只能对
相关候选排序，不能凭空创造相关性。

当前排序公式：

```text
score = 0.55 relevance
      + 0.14 confidence
      + 0.12 importance
      + 0.10 trust
      + 0.05 exp(-age_days / 180)
      + 0.04 scope_specificity
```

返回值不是纯文本，而是 content、attributes、score、trust、validity、Evidence 和潜在 conflict
IDs。若没有可靠结果则返回空 list；Agent 不获得“可补全”的 placeholder。

第一版没有伪装成 vector search。现有 metadata + lexical 方法是可离线重放的 baseline；后续可以
在第一阶段增加 embedding candidate retrieval，但必须保留相同 trust filter、reranker、threshold
和 ablation contract。

## 8. Context Engine 接入

`DynamicAgentState` 增加 user/session/goal/knowledge-node scope。`ContextRequest.memory_query` 由
Plan objective 与 current Step objective 生成，`ContextCompiler` 调用 `MemoryRetriever`，将结果
放入独立 `memory` partition：

```text
DynamicAgentState scope + current Step
              │
              ▼
          MemoryQuery
              │
              ▼
 active scoped recall + rerank + Evidence
              │
              ▼
  ContextPackage.values["memory"]
  ContextSnapshot.source_ids = memory:<id>
```

Memory priority 为 88，低于 mandatory policy/evidence，高于 recent Observation；它是 optional
partition，Token 不足时可以单独 eviction，并在 `ContextTruncation(partition="memory")` 记录来源。
Memory 内容和 Evidence 参与 Context input/output hash，因此 Snapshot 可以证明一次模型调用实际使用
了哪个版本的长期信息。

`LEARNLOOP_AGENT_MEMORY_ENABLED=false` 可以完全关闭该 partition 做 ablation，不改变 Planner、
Tool allowlist 或 Verifier。

## 9. Run 完成与幂等性

完成路径产生 `memory_extracted` Agent Event，包含 action、memory ID、最终状态和治理原因。
Memory 写入是已验证 Run 的 secondary durable projection：若业务数据库暂时不可用，Trace 记录
失败，但不会把已经通过 Verifier 的学习 Run 反向改为失败。

重试时 exact fingerprint 会命中同一记录并追加 Evidence，因此 crash replay 不会创建多个相同
Episode。时间源通过 `clock` dependency injection，expiry 和离线评测可以 deterministic replay。

## 10. API 与治理 UI

API：

- `GET /memories`：按 status/kind 浏览；
- `GET /memories/search`：执行与 Agent 相同的可信检索；
- `GET /memories/{id}`：查看内容、Evidence 与 Revision；
- `POST /memories/candidates`：显式提交候选；
- `POST /memories/{id}/approve|reject|deactivate`：状态治理；
- `PATCH /memories/{id}`：创建 superseding correction candidate；
- `DELETE /memories/{id}`：物理删除 Record、Evidence 和 Revision。

前端 `/memories` 页面展示“系统记住了什么”，支持状态筛选、来源展开、审批、拒绝、停用、纠正和
删除。更正 UI 明确提示：它先产生候选，批准前旧 Active Memory 继续生效。

## 11. 配置与 Ablation

```text
LEARNLOOP_AGENT_MEMORY_ENABLED=true
LEARNLOOP_AGENT_MEMORY_RECALL_LIMIT=6
LEARNLOOP_AGENT_MEMORY_MINIMUM_SCORE=0.24
```

`enabled` 用于有/无 Memory 对照；`recall_limit` 控制 Context fan-in；`minimum_score` 冻结
abstention gate。所有配置都有 Pydantic range validation。

## 12. LongMemEval-style 评测

`evals/datasets/memory_v1.json` 覆盖：

- cross-session fact recall；
- preference temporal update；
- unrelated no-answer；
- deletion 与 expiry forgetting；
- Tool prompt injection；
- low-confidence inference。

冻结门禁包含 recall accuracy、abstention rate、write safety、false recall rate 和 task success lift。
确定性 service tests 另外执行真实 ingestion/retrieval/state transition；冻结 JSON 用于版本化对照与
CI threshold regression，不代表生产模型统计。

```bash
python scripts/run_evals.py \
  --dataset backend/evals/datasets/memory_v1.json \
  --output backend/evals/reports/memory-latest.json
```

## 13. Failure semantics

- 不可信、低置信、PII candidate：`rejected`，保留最小审计链但不可召回；
- 高影响 candidate：等待审批，不能进入 Context；
- stale preference：新值批准后旧值 `expired`；
- 到期：读取前执行 expiry projection，此后不可召回；
- 删除：hard delete 并级联 Evidence/Revision；
- 无 lexical relevance / 低于阈值：空集合；
- Memory partition 超预算：仅裁剪 Memory 并写 Snapshot，不破坏 mandatory Context；
- Memory projection 写失败：Event 可见，已验证 Run 仍完成；
- 直接批准非 candidate：`409 conflict`。

## 14. 代码阅读顺序

1. `app/domain/memory/models.py`：aggregate、trust、status 和时间模型；
2. `app/agent/memory/service.py`：governance pipeline、supersession 和 reranker；
3. `app/infrastructure/database/models.py` 与 `repositories.py`：transactional persistence；
4. `app/agent/dynamic/context.py`：Memory partition、provenance 和 Token packing；
5. `app/agent/dynamic/kernel.py`：Run completion extraction boundary；
6. `app/api/routes/memories.py` 与前端 `/memories`：用户治理面；
7. `tests/test_agent_memory.py`、`test_context_engine.py`：安全与 Context 行为；
8. `evals/datasets/memory_v1.json`：冻结场景与门禁。

复杂函数和类中的注释重点解释 Policy Enforcement Point、stable fingerprint、temporal
supersession、two-stage retrieval、abstention、secondary projection 和 CJK bigram 等高级机制。

## 15. 验收结论与边界

本阶段的 engineering gates 已覆盖：偏好替代、注入阻断、低置信阻断、PII 阻断、无答案、过期、
删除、Evidence/Revision、Context provenance、API 与 UI。Memory-enabled 的线上任务成功率和真实
模型错误召回率仍需使用固定 Scenario、真实 Session 和足够样本做 paired ablation 后才能声称统计
收益；本实现提供了开关、指标结构和冻结数据集，不虚构线上效果。

在进入 Stage 14 前，应保持这条 invariant：RAG/Web/Tool 检索内容永远只是 untrusted Evidence，
不能直接改变系统政策、Tool 权限或 Semantic/Procedural Memory。
