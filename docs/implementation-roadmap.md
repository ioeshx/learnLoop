# LearnLoop 第一版实现流程

## 1. 实施原则

LearnLoop 不按照“先做完整数据库，再做完整后端，最后做前端”的方式开发，而是按可运行的垂直闭环推进。

总体顺序：

```text
工程启动
→ 数据与领域模型
→ 无 LLM 的最小学习闭环
→ 模型接入
→ LangGraph 工作流
→ Interrupt / SSE / 恢复
→ 资料处理与 RAG
→ 后台任务
→ 自适应学习与复习
→ 评测和工程完善
```

每个阶段都必须满足：

- 有明确输入和输出。
- 有自动化测试。
- 有可运行的阶段成果。
- 不依赖尚未实现的高级模块。
- 出现问题时可以独立定位到当前层。

## 2. 阶段一：工程启动

目标是让前后端在 Windows、macOS 和 Linux 上使用相同方式启动，不实现 Agent 业务。

### 2.1 后端初始化

优先实现：

```text
backend/pyproject.toml
backend/app/main.py
backend/app/api/
backend/app/infrastructure/
backend/tests/
```

内容：

1. 初始化 Python 3.12 和 uv 项目。
2. 安装 FastAPI、Pydantic、Uvicorn 和测试依赖。
3. 建立配置加载机制。
4. 建立结构化日志。
5. 定义统一错误响应。
6. 实现 `/health` 接口。
7. 配置 pytest、Ruff 和类型检查。

验收：

- FastAPI 可以正常启动。
- `/health` 返回版本和运行状态。
- 缺少配置时输出明确错误。
- 后端单元测试和静态检查可以执行。

### 2.2 前端初始化

优先实现：

```text
frontend/package.json
frontend/src/app/
frontend/src/components/
frontend/src/lib/
```

内容：

1. 初始化 Next.js、React 和 TypeScript。
2. 配置 Tailwind CSS。
3. 建立基础页面布局。
4. 建立统一 API Client。
5. 建立前端环境变量配置。
6. 创建后端状态检查页面。

验收：

- Next.js 可以正常启动。
- 前端能够调用 `/health`。
- API 不可用时展示明确错误，而不是白屏。

### 2.3 跨平台启动脚本

优先实现：

```text
scripts/dev.py
scripts/migrate.py
```

`dev.py` 最终负责启动：

- FastAPI API。
- Next.js 开发服务器。
- 后续加入的后台 Worker。

避免把 Bash、Make 或 PowerShell 作为唯一启动方式。

## 3. 阶段二：SQLite 与领域模型

目标是建立可测试的学习领域，不依赖 LLM 和 LangGraph。

### 3.1 SQLite 基础设施

优先实现：

```text
backend/app/infrastructure/database/
backend/migrations/
```

内容：

1. SQLAlchemy Engine 和 Session。
2. `learnloop.db` 路径配置。
3. Alembic 数据库迁移。
4. SQLite WAL、Foreign Key 和 `busy_timeout`。
5. Repository 接口和事务边界。
6. 测试用临时 SQLite 数据库。

先建立最小表集：

```text
users
learning_goals
knowledge_nodes
knowledge_edges
study_plans
plan_items
study_sessions
exercises
exercise_attempts
mastery_events
mastery_snapshots
review_schedules
```

不要一次性创建规划中的所有 Agent Trace 和评测表；在对应功能实现时再添加。

### 3.2 领域模型顺序

按以下顺序实现：

```text
goals
→ knowledge
→ study plan
→ exercises
→ mastery
→ review
```

对应目录：

```text
backend/app/domain/goals/
backend/app/domain/knowledge/
backend/app/domain/exercises/
backend/app/domain/mastery/
backend/app/domain/review/
```

具体能力：

1. 创建和更新学习目标。
2. 创建知识点与前置依赖。
3. 检测知识图中的环。
4. 创建阶段计划和计划项。
5. 创建练习与作答记录。
6. 对客观题进行确定性判分。
7. 写入 Mastery Event。
8. 从事件计算当前掌握度。
9. 使用 FSRS 生成复习时间。

领域逻辑必须是纯 Python，不依赖 FastAPI、LangChain 或 LangGraph。

验收：

- 不使用 LLM 也能创建一套完整学习计划。
- 完成固定选择题后，掌握度正确变化。
- 能够生成复习时间。
- 知识依赖图出现环时拒绝保存。

## 4. 阶段三：无 LLM 的垂直闭环

目标是先完成一个普通但可靠的学习系统：

```text
创建目标
→ 获取固定计划
→ 开始学习
→ 阅读固定讲解
→ 完成固定练习
→ 判分
→ 更新掌握度
→ 安排复习
```

### 4.1 Application Service

在 `backend/app/application/` 中优先实现：

- `CreateLearningGoal`
- `CreateStudyPlan`
- `StartStudySession`
- `SubmitExerciseAttempt`
- `CompleteStudySession`
- `GetDueReviews`

Application Service 负责调用领域模型、控制数据库事务和保证幂等，不包含 HTTP 逻辑，也不直接调用 LLM。

### 4.2 第一批 API

按顺序实现：

```text
POST /goals
GET  /goals/{id}
POST /goals/{id}/plans
GET  /plans/{id}
POST /study-sessions
GET  /study-sessions/{id}
POST /study-sessions/{id}/attempts
GET  /reviews/due
```

### 4.3 最小前端

只实现四个页面：

1. 创建学习目标。
2. 查看学习计划。
3. 完成一次学习。
4. 查看作答结果。

验收：

- 前后端数据结构一致。
- 刷新页面不会丢失业务状态。
- API 能完整完成学习闭环。
- SQLite 事务和错误回滚正确。

## 5. 阶段四：LLM 接入

目标是在不改变领域规则的前提下，让模型生成计划、讲解和练习。

### 5.1 结构化输出

先定义 Schema，再编写 Prompt：

- `GoalClarification`
- `KnowledgeGraphProposal`
- `StudyPlanProposal`
- `LessonContent`
- `ExerciseProposal`
- `AnswerEvaluation`
- `MisconceptionDiagnosis`
- `StudySummary`

所有结果必须经过 Pydantic 校验。业务代码不能依赖对任意 Markdown 的字符串解析。

### 5.2 模型抽象

在 `backend/app/infrastructure/llm/` 中按顺序实现：

1. 模型能力接口。
2. `FakeModelProvider`。
3. 基于 Fake Provider 的自动化测试。
4. 真实模型 Provider，例如 DeepSeek。
5. 超时、重试和 Token 统计。

测试默认使用 Fake Provider，避免测试不稳定、API 费用和 CI 密钥依赖。

### 5.3 Prompt 管理

在 `backend/app/agent/prompts/` 中按顺序实现：

1. 目标澄清。
2. 知识点和依赖生成。
3. 学习计划生成。
4. 讲解生成。
5. 练习生成。
6. 简答题评价。

每个 Prompt 应具有唯一名称、版本、输入 Schema、输出 Schema、使用场景和测试样例。

验收：

- 模型可以生成结构化计划和练习。
- 非法输出最多进行一次受限修复。
- 结构化结果通过领域规则后才能写入数据库。

## 6. 阶段五：LangGraph 核心工作流

目标是将 LLM 和领域服务组成可控、可测试的 Agent 工作流。

### 6.1 Agent State

首先在 `backend/app/agent/states/` 中定义：

- `StudySessionState`
- `GoalPlanningState`
- `ResourceIngestionState`
- `ReviewSessionState`

State 只保存运行需要的数据和业务 ID，不保存 ORM 对象、连接、Provider Client 或完整文档。

### 6.2 Tool

然后在 `backend/app/agent/tools/` 中实现：

- `get_learning_goal`
- `get_mastery_state`
- `get_due_reviews`
- `search_learning_resources`
- `validate_knowledge_graph`
- `save_plan_proposal`
- `create_exercise`
- `grade_objective_answer`
- `update_mastery`
- `schedule_review`

Tool 应薄封装 Application Service，不在 Tool 内重新实现业务规则。

### 6.3 Node

在 `backend/app/agent/nodes/` 中先实现确定性节点：

- 加载学习状态。
- 选择知识点。
- 更新掌握度。
- 安排复习。
- 保存总结。

再实现 LLM 节点：

- 生成讲解。
- 生成练习。
- 评价简答题。
- 诊断错误。
- 生成补救内容。

### 6.4 每日学习图

第一个实现的 Graph：

```text
load_context
→ select_concepts
→ retrieve_sources
→ generate_lesson
→ wait_for_answer
→ evaluate_answer
→ route_by_result
→ update_mastery
→ schedule_review
→ save_summary
```

分支：

```text
掌握     → 完成本知识点
部分掌握 → 补充讲解 → 再练一次
未掌握   → 前置知识补救 → 再练一次
```

补救循环最多两次，不允许无限反思。

### 6.5 目标规划图

第二个实现的 Graph：

```text
understand_goal
→ check_missing_information
→ diagnostic
→ build_knowledge_graph
→ validate_graph
→ generate_plan
→ wait_for_approval
→ persist_plan
```

完成每日学习图和目标规划图后，再实现 Resource Ingestion Graph 和 Review Graph。

## 7. 阶段六：Checkpoint、Interrupt 与 SSE

### 7.1 SQLite Checkpoint

接入：

```text
data/db/checkpoints.db
```

实现：

- `AsyncSqliteSaver`。
- `study_session_id` 和 `thread_id` 映射。
- Graph 创建、读取和恢复。
- Checkpoint 清理规则。

必须测试：

1. Graph 执行到等待作答。
2. 关闭 Checkpointer，模拟应用退出。
3. 重新启动应用。
4. 使用相同 `thread_id` 恢复。
5. 提交答案并完成后续节点。

### 7.2 Human-in-the-loop

按顺序加入 Interrupt：

1. 等待用户回答练习。
2. 等待用户批准计划。
3. 用户修改计划。
4. 用户质疑或纠正评分。
5. 资料解析存在歧义时等待确认。

### 7.3 SSE

统一事件结构：

```json
{
  "run_id": "run_123",
  "sequence": 12,
  "event": "node_started",
  "node": "generate_exercise",
  "timestamp": "...",
  "data": {}
}
```

第一版事件类型：

- `run_started`
- `node_started`
- `node_completed`
- `tool_started`
- `tool_completed`
- `interrupt_created`
- `run_completed`
- `run_failed`

前端随后实现当前步骤、工具状态、等待输入、失败提示和 SSE 断线重连。

## 8. 阶段七：本地文件与 RAG

RAG 是 Agent 的工具，不是产品起点。应在学习闭环稳定后实现。

### 8.1 本地文件存储

在 `backend/app/infrastructure/storage/` 中按顺序实现：

1. `DocumentStorage` 接口。
2. `LocalDocumentStorage`。
3. SHA-256 去重。
4. 临时文件与原子移动。
5. 删除和回收机制。
6. 路径安全检查。

### 8.2 同步资料处理

先用同步函数跑通：

```text
保存文件
→ 解析文本
→ 分块
→ 保存 Chunk
→ 建立 FTS5 索引
```

格式实现顺序：

1. TXT。
2. Markdown。
3. PDF。
4. 单个网页 URL。

不要四种格式同时开发。

### 8.3 Embedding 和混合检索

实现顺序：

1. Embedding Provider 接口。
2. Fake Embedding。
3. 真实 Embedding Provider。
4. SQLite 保存向量。
5. NumPy 余弦相似度。
6. FTS5 关键词检索。
7. Reciprocal Rank Fusion。
8. 引用页码和章节。

验收：

- 固定问题的正确资料块进入 Top K。
- 讲解和练习引用具体页码或章节。
- 没有资料支持时不生成伪造引用。

## 9. 阶段八：后台任务

同步资料处理稳定后，再迁移到 Worker。

### 9.1 SQLite Job 系统

在以下位置实现：

```text
backend/app/workers/
scripts/run_worker.py
```

顺序：

1. `background_jobs` 表。
2. Job Handler Registry。
3. 单 Worker 轮询。
4. 任务领取和租约。
5. 幂等键。
6. 重试和指数退避。
7. 取消和失败状态。
8. 任务进度。

第一批后台任务：

- 文档解析。
- Embedding。
- 周报生成。
- 到期复习任务生成。

第一版不实现多 Worker。

## 10. 阶段九：自适应学习与复习

### 10.1 掌握度模型

顺序：

1. 简单、可解释的规则模型。
2. Mastery Event。
3. 当前掌握度投影。
4. 难度调整。
5. 前置知识补救。
6. 用户纠正后的重新计算。

初始规则示例：

```text
首次正确       +0.15
补救后正确     +0.08
完全错误       -0.10
复习后仍正确   +0.12
用户纠正评分   重新计算对应事件
```

第一版不实现复杂的 Bayesian Knowledge Tracing。

### 10.2 FSRS

实现：

- 创建复习卡片。
- 根据作答更新 FSRS 状态。
- 查询到期复习项。
- 计算复习优先级。
- 错过复习后的重新安排。

LLM 负责生成复习题，FSRS 负责决定复习时间。

### 10.3 补救路径

```text
评估答案
→ 识别错误知识点
→ 检查前置依赖
→ 选择补救内容
→ 生成较简单的练习
→ 最多重试两次
```

这是 LearnLoop 第一版最重要的 Agent 差异化能力之一。

## 11. 阶段十：评测、可观测性与完善

评测骨架从早期存在，但完整评测在核心功能稳定后完成。

### 11.1 Agent Trace

添加：

```text
agent_runs
agent_events
tool_calls
prompt_versions
```

记录：

- Graph 和节点。
- 模型和 Prompt 版本。
- Tool 参数与结果摘要。
- Token 和耗时。
- 重试、错误和 Interrupt。

不记录模型隐藏思维过程。

### 11.2 离线评测

按顺序建立：

1. 结构化输出有效率。
2. Tool 选择和参数正确率。
3. Checkpoint 恢复率。
4. RAG Recall@K 和 MRR。
5. 引用支持率。
6. 客观题评分正确率。
7. 简答题评分一致性。
8. 补救路径成功率。
9. Token、延迟和费用。

### 11.3 前端完善

最后实现：

- Dashboard。
- 知识图谱。
- 掌握度趋势。
- 学习周报。
- Agent Trace 页面。
- PWA Manifest 和安装体验。
- 数据导入、导出与备份。

## 12. 推荐时间线

| 周期 | 主要内容 | 阶段成果 |
| --- | --- | --- |
| 第 1 周 | 前后端初始化、SQLite | 项目可启动 |
| 第 2 周 | 领域模型、固定学习闭环 | 无 LLM 的可用系统 |
| 第 3 周 | 模型接口、结构化输出 | LLM 能生成计划和练习 |
| 第 4 周 | Study Graph、Goal Graph | Agent 核心流程 |
| 第 5 周 | Checkpoint、Interrupt、SSE | 可暂停恢复的交互 Agent |
| 第 6 周 | 文件、FTS5、Embedding、RAG | 基于个人资料学习 |
| 第 7 周 | Worker、掌握度、FSRS | 自适应和复习闭环 |
| 第 8 周 | 评测、Trace、E2E、PWA | 第一版完整交付 |

时间线是建议顺序，不是硬性工期。每个阶段以上一阶段验收通过为进入条件。

## 13. 三个核心里程碑

### 13.1 里程碑 A：无 AI 闭环

```text
固定计划 → 固定讲解 → 作答 → 判分 → 掌握度 → 复习
```

证明数据库、领域模型、API 和前端可靠。

### 13.2 里程碑 B：可恢复 Agent 闭环

```text
LLM 计划 → 用户批准 → 学习 → Interrupt → 作答 → 补救 → 恢复
```

证明 Agent 编排、持久化和 Human-in-the-loop 可靠。

### 13.3 里程碑 C：个人资料闭环

```text
导入资料 → RAG → 带引用讲解 → 练习 → 评估 → 自适应复习
```

证明 LearnLoop 对个人学习产生实际价值。

## 14. 不推荐的实现顺序

不要：

- 一开始实现多个 Agent。
- 先写大量 Prompt，再设计领域和数据模型。
- 先做完整 RAG，再做学习闭环。
- 先做漂亮前端，而后端只有聊天接口。
- 把业务状态全部放进 LangGraph Checkpoint。
- 让所有测试调用真实模型。
- 在 API 进程直接执行模型生成的任意代码。
- 一次性创建规划中的所有数据库表。
- 在同步资料处理尚未稳定时先做任务队列。

## 15. 第一批实际开发任务

正式开始开发时，按以下提交顺序推进：

1. 初始化 FastAPI、配置、日志、健康检查和后端测试。
2. 初始化 Next.js、基础布局和后端状态检查。
3. 建立 SQLAlchemy、Alembic 和临时数据库测试。
4. 实现 Goal、Knowledge Node 和 Knowledge Edge。
5. 实现知识图环检测。
6. 实现 Study Plan 和 Plan Item。
7. 实现 Exercise、Attempt 和客观题评分。
8. 实现 Mastery Event、Snapshot 和基础 FSRS。
9. 实现无 LLM 的 Application Service 和 API。
10. 实现最小前端学习闭环。
11. 定义 LLM 结构化输出和 Fake Provider。
12. 接入第一个真实模型 Provider。
13. 实现 Study Graph。
14. 实现 Goal Planning Graph。
15. 接入 SQLite Checkpoint、Interrupt 和 SSE。
16. 实现本地文件、资料解析和 FTS5。
17. 实现 Embedding 和混合检索。
18. 将资料处理迁移到 SQLite Worker。
19. 实现自适应补救路径和复习闭环。
20. 完成 Agent Trace、离线评测、E2E 和 PWA。

最先开始的主线始终是：

> 项目启动与 SQLite → 领域模型 → 无 LLM 学习闭环 → LLM 结构化输出 → LangGraph 学习图。

