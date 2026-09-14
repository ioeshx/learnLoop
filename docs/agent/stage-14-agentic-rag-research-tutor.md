# LearnLoop v2 Stage 14：Agentic RAG 与 Research Tutor 实现说明

本文记录 Stage 14 的实际实现。重点不是把一次 `search()` 包装成 Agent，而是建立一个受预算、
Evidence Gate 和 Citation Verifier 共同约束的 Research Harness：它可以主动判断是否检索、拆解
问题、追踪证据缺口和改写 Query，但不能用模型输出绕过资料作用域、证据质量门或引用验证。

## 1. 交付结果

Stage 14 完成了四层闭环：

1. `EvidenceItem / EvidenceGrade / Claim / CitationLink` 类型化证据图；
2. `no_retrieval / single_retrieval / multi_step_research` 路由和 SubQuestion 分解；
3. 有轮数、Query、来源、读取字符和 Context Token 上限的 Gap-driven Loop；
4. atomic Claim synthesis、独立 Citation Verifier、持久化 Research Trace、动态 Agent Tool、API
   和 Research Tutor UI。

核心执行路径如下：

```text
User Question + goal/node scope
              │
              ▼
       deterministic router
  no retrieval / single / multi-step
              │
              ▼
   conservative SubQuestion graph
              │
              ▼
┌──── budgeted gap-driven loop ────────────────────────────┐
│ unique Query → local hybrid RAG → untrusted Evidence     │
│      ▲                           │                       │
│      └──── Query rewrite ← coverage/gap check ← grading  │
└──────────────────────────────────────────────────────────┘
              │ accepted Evidence only
              ▼
  model-backed or extractive Claim proposals
              │
              ▼
 deterministic Citation Verifier
              │
       ┌──────┴────────┐
       ▼               ▼
 verified answer   complete Research Trace
       │           including rejected Evidence
       ▼
 safe Tool projection → Stage 12 Observation/Context
```

## 2. Agent 与 Harness 的职责边界

本阶段采用 `Model proposes, code disposes`：

| 决策 | 所有者 | 原因 |
|---|---|---|
| 资料作用域 | `RagService.validate_scope()` | 模型不能扩大 goal/node scope |
| Retrieval routing | `ResearchPolicy` | 简单、可重放、零检索路径可验证 |
| Query 去重与预算 | `ResearchTutor` | 预算是 Policy，不是 Prompt 建议 |
| Evidence trust/grade | `ResearchPolicy` | Retrieved text 不能自我声明可信 |
| Claim proposal | Structured Model 或 extractive fallback | 语言合成适合模型，但只产生候选 |
| Citation support | `CitationVerifier` | 模型声明的 evidence ID 仍需独立验证 |
| 是否进入答案 | `CitationVerifier` + Harness | unsupported Claim 不得进入答案 |
| 是否写 Memory | Stage 13 `MemoryService` | Research Tool 没有 Memory mutation capability |

`ResearchTutor` 是单 Agent 内核可调用的只读 Tool，而不是 Stage 15 Subagent。它没有独立的 Tool
allowlist、子 Run 或并行生命周期；因此本阶段没有提前引入多 Agent 的委派复杂度。

## 3. Evidence、Claim 与 Citation Graph

### 3.1 EvidenceItem

每个检索 Chunk 被提升为 immutable Evidence，记录：

- `query_id / subquestion_id`：为什么检索到它；
- `resource_id / chunk_id / page_number / section / source_uri`：原始定位；
- `resource_version / resource_sha256 / content_sha256`：版本和完整性；
- `trust=untrusted`：资料内容永远不是 Agent instruction；
- `EvidenceGrade`：相关性、来源质量、重复度、覆盖度、判定和原因。

`resource_sha256` 标识输入资料版本，`content_sha256` 标识实际送入研究循环的受限 excerpt。二者同时
保存，既能发现资料更新，也能重放“当时究竟读了哪段内容”。

### 3.2 Claim 与 CitationLink

Synthesizer 先输出 atomic `ClaimDraft`，每条只允许携带真实 `evidence_id`。Verifier 再生成：

- `Claim.citation_status`：`supported / partially_supported / unsupported`；
- `Claim.included_in_answer`：最终答案准入位；
- `CitationLink`：`claim_id → evidence_id → chunk_id → resource_id`；
- `explanation`：当前 deterministic entailment proxy 的可审计分数。

重要 Claim 如果 unsupported，会保留在 Trace 供调试，但 `included_in_answer=false`。如果全部 Claim
都被拒绝，即使 Synthesizer 产生过文本，Research Run 仍为 `insufficient_evidence`。

### 3.3 ResearchTrace 不变量

Pydantic graph validation 拒绝引用未知 Claim 或 Evidence 的 Citation。完整 Trace 保存 Request、
Mode、SubQuestion、Query lineage、全部 Evidence、Claim、Citation、Gap、Usage 和答案。Trace 是公开
轨迹，不保存 hidden chain-of-thought。

## 4. Retrieval Routing 与问题分解

`ResearchPolicy.route()` 将请求分为：

- `no_retrieval`：问候、感谢等不需要资料的问题，保证 RAG 调用数为零；
- `single_retrieval`：一个可直接检索的事实或概念；
- `multi_step_research`：比较、区别、因果、组合问题或较长请求。

多步问题最多产生四个 `SubQuestion`。`depends_on` 保留问题关系，拆分策略刻意保守：子问题来自用户
原文或明确的“适用条件与限制”facet，避免一个开放式 Planner 静默扩大研究范围。

每个 `ResearchQuery` 保存 `subquestion_id / round_no / parent_query_id`。Harness 对规范化 Query 做
全局去重；第二轮以后只为未覆盖 SubQuestion 生成 rewrite，不会机械重复所有检索。

## 5. Gap-driven Research Loop

循环固定为：

```text
uncovered SubQuestions
→ generate unique Queries
→ local hybrid retrieval
→ Evidence grading
→ recompute coverage
→ stop, rewrite, or declare gaps
```

当前复用 Stage 9 的本地 FTS5 + vector candidate retrieval 和 RRF 排序。第一版不会访问开放 Web；
网页只有先通过现有安全抓取和资源摄取流程变成本地 Resource 后，才可进入 Research scope。

Evidence Gate 依次检查：

1. injection-like instruction；
2. lexical relevance/coverage；
3. source quality；
4. 与已接受 Evidence 的 near-duplicate；
5. 通过后标记 `accepted`。

中文相关性使用 CJK bigram，英文使用 word token。覆盖分数以 Query terms 为分母，避免长而相关的
Chunk 因 union denominator 被系统性低估。Prompt Injection 检查先于相关性：即使恶意 Chunk 同时
包含正确知识，它也不能进入 Claim synthesis。

## 6. Hard Budget 与终止语义

`ResearchBudget` 同时约束：

- `max_rounds`；
- `max_queries`；
- `max_sources`；
- `max_read_chars`；
- `max_context_tokens`。

Harness 在每次检索和读取前检查预算，并将 `rounds / queries / sources / read_chars /
estimated_tokens / stopped_reason` 写入 Trace。Token 估算用于离线、本地、无 tokenizer 环境下的硬
上限；它不是供应商账单 Token。达到预算不是异常，也不会补写答案，而是输出已验证部分和明确 Gap。

默认值由以下环境变量控制，并有 Pydantic range validation：

```text
LEARNLOOP_RESEARCH_MAX_ROUNDS=3
LEARNLOOP_RESEARCH_MAX_QUERIES=8
LEARNLOOP_RESEARCH_MAX_SOURCES=12
LEARNLOOP_RESEARCH_MAX_READ_CHARS=30000
LEARNLOOP_RESEARCH_MAX_CONTEXT_TOKENS=8000
```

## 7. Claim Synthesis 与独立验证

配置 Structured Model 时，`ModelResearchSynthesizer` 使用版本化 Prompt 输出 `ClaimDraftSet`。
Prompt 明确把 Evidence 定义为 untrusted data，并要求只引用传入 ID。没有模型 Provider 时，
`ExtractiveResearchSynthesizer` 只复制每个 SubQuestion 的首个 accepted Evidence sentence，提供可离线
运行和可重放的安全 fallback。

两条路径都不能直接写最终答案。`CitationVerifier` 会忽略不存在或未通过 Gate 的 evidence ID，重新
计算 Claim 与 Evidence 的 lexical support，再决定 supported、partial 或 unsupported。partial Claim
以不确定措辞进入答案；unsupported Claim 被移除。

当前 Verifier 是 deterministic lexical entailment proxy，而不是完整 NLI。这使测试稳定且决策可
解释，但对 paraphrase、数值矛盾和多证据联合推理仍有限制。后续可以增加 NLI verifier 或规则化
numeric/date checker，但必须保留现有 Claim graph 和 fail-closed 准入语义。

## 8. Prompt Injection 与 Context 隔离

Research 有两个不同视图：

1. durable Trace 保存 accepted 和 rejected Evidence，便于审计为什么淘汰资料；
2. `research.ask` Tool projection 只返回 included Claim、非 unsupported Citation 和 accepted
   Evidence。

这一区分解决了“低分资料要可观察，但不能进入答案 Context”的矛盾。如果直接把完整
`ResearchResult` 作为 Tool result，rejected Chunk 会被 Stage 12 当作 Observation Artifact 再次送入
模型，等于绕过 Evidence Gate。现在动态 Agent Context 只能看到 safe projection；恶意原文仅存在于
权限受控的 Research Trace/API/UI。

Research Tool 为 `read_only=true`、`idempotent=true`，且没有 Tool Registry、Policy 或 Memory 写
接口。Stage 13 仍只在 verified Run lifecycle boundary 提取 Memory；资料中的“记住我”“扩大权限”
不会改变这条 Policy Enforcement Point。

## 9. 持久化与迁移

Migration `0008_agentic_research` 创建 `research_runs`：

- relational envelope 保存 user、goal、可选 node、mode、status 和时间，支持范围查询；
- `trace_json` 原子保存类型化 graph，避免读取到半写入的 Claim/Citation 关系；
- user/goal 删除使用 cascade，node 删除使用 `SET NULL`；
- Check Constraint 冻结 mode 和 status 枚举；
- user/goal + created time 索引支持历史列表。

Research Trace 属于业务可查询数据，因此进入 `learnloop.db`，不写 LangGraph checkpoint。降级
`0008 → 0007` 会删除 `research_runs` 表；执行前应导出需要保留的研究轨迹。

## 10. 动态 Agent、API 与 UI 接入

动态 Planner/Decision Prompt 可为比较、复杂或证据型问题选择只读 `research.ask`。Tool 完成后，
safe projection 继续走 Stage 11 的 Tool Observation 和 Stage 12 的 Artifact/Context 路径，因此已有
allowlist、timeout、result truncation、Trace 和 Token packing 机制仍然有效。

Research API：

- `POST /research/runs`：执行 scoped Research；
- `GET /research/runs?goal_id=...`：按用户和目标列出历史；
- `GET /research/runs/{trace_id}`：读取完整证据图。

前端 `/research` 展示运行模式和预算、verified answer、unresolved gaps、atomic Claims、Citation
状态、accepted/rejected Evidence、质量分数和 content hash。Claim 中的 Citation 可页内跳转到对应
Evidence，便于代码审查和学习证据链。

## 11. 评测与测试门禁

`evals/datasets/research_v1.json` 冻结以下维度：

- route accuracy；
- multi-hop Recall；
- citation support accuracy；
- unsafe Evidence exclusion；
- no-retrieval efficiency；
- insufficient-evidence accuracy；
- agentic vs single-shot task success lift；
- average estimated Tokens。

运行：

```bash
python scripts/run_evals.py \
  --dataset backend/evals/datasets/research_v1.json \
  --output backend/evals/reports/research-latest.json
```

Service tests 使用 Fake RAG 验证零检索、多跳拆解、Gap rewrite、Query budget、Injection、unsupported
Claim 和 safe Tool projection。API 集成测试执行真实 Markdown ingestion、后台索引、本地 hybrid
retrieval、Research Run、Trace 查询和 Citation mapping。Migration tests 验证从 base 升级到
`0008_agentic_research`。

冻结 JSON 是版本化 engineering gate，不是生产统计结论。它能阻止已知行为回归，但“多跳 Recall
和答案正确率显著优于 single-shot”的真实收益仍需固定真实问题集、相同资源库和相同 Token 预算的
paired evaluation 后才能宣称。

## 12. Failure Semantics

- 不需要资料：`completed + no_retrieval`，零 RAG Query；
- 未检索到可靠 Evidence：`insufficient_evidence`，返回明确 Gap；
- Prompt Injection / 低相关 / 低质量 / duplicate：留在 Trace，不进入 synthesis 或 Agent Context；
- 部分支持：以不确定措辞保留，并维持 Citation；
- unsupported 重要 Claim：从答案移除；全部被移除时状态不得 completed；
- 达到预算：停止 rewrite，不突破上限，保留 `stopped_reason`；
- 未知 goal/node scope：沿用 Application error，不执行越界检索；
- Trace 原子写入：成功响应前持久化完整 graph，避免返回不可追踪答案。

## 13. 代码阅读顺序

1. `app/agent/research/models.py`：Evidence/Claim/Citation/Trace contracts；
2. `app/agent/research/policy.py`：routing、decomposition、grading、Citation Verifier；
3. `app/agent/research/service.py`：Gap-driven Loop、hard budgets、answer composition；
4. `app/agent/prompts/research.py` 与 `research/synthesis.py`：structured Claim proposal；
5. `app/agent/research/store.py` 和 migration `0008`：atomic durable Trace；
6. `app/agent/dynamic/tools.py`：`research.ask` safe Context projection；
7. `app/api/routes/research.py` 与前端 `/research`：审计和学习界面；
8. `tests/test_agentic_research.py` 与 `evals/datasets/research_v1.json`：安全和质量门禁。

复杂函数和类中的注释集中解释 Trust Boundary、Policy Enforcement Point、Gap-driven Loop、
immutable Evidence、safe projection、atomic Trace 和 deterministic verification，而不是逐行翻译代码。

## 14. 验收结论与后续边界

14A～14D 所需的对象、Research Loop、Citation verification、Trace、API/UI 和冻结评测均已实现。
本阶段严格保持三条 invariant：外部开放 Web 不在检索面；rejected Evidence 不进入 Agent Context；
Research 结果不能直接写 Semantic/Procedural Memory。

进入 Stage 15 前应先用真实学习任务做同预算 paired evaluation。如果单 Agent Research Harness 已在
目标延迟内满足质量要求，就不应仅为架构完整性拆成 Subagent；只有独立研究上下文、墙钟并行或专业
Verifier 被失败聚类证明为实际瓶颈时，再引入 Subagent-as-Tool。
