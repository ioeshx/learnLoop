# LangGraph 核心工作流

## 范围

阶段五使用 LangGraph `StateGraph` 编排模型能力和 Application Service，包含：

- 每日学习图。
- 目标规划图。
- 四类可序列化 State 合约。
- 通过 Runtime Context 注入 Tool 和模型，不把数据库连接或 Provider 放入 State。

Checkpoint、真正的 `interrupt()`、SSE 和跨进程恢复属于阶段六。本阶段的
`wait_for_answer`、`check_missing_information` 和 `wait_for_approval` 是显式门：输入缺失时
Graph 结束并返回等待状态，调用方补充输入后重新调用 Graph。

## 每日学习图

```text
load_context
→ select_concepts
→ retrieve_sources
→ generate_lesson
→ generate_exercise
→ wait_for_answer ── 无答案 ──→ END(awaiting_answer)
→ evaluate_answer
→ route_by_result
→ update_mastery
→ schedule_review
├─ mastered / remediation_exhausted → save_summary → END
└─ wrong → diagnose_error
   ├─ 第一次错误 → generate_supplemental → wait_for_answer
   └─ 第二次错误 → generate_prerequisite_remediation → wait_for_answer
```

补救次数默认最多两次。第三次错误会得到 `remediation_exhausted` 并结束会话，避免无限
反思。客观题由领域代码判分；掌握度事件和 FSRS 复习时间由同一个 Application Service
事务写入。`run_id + attempt_number` 组成幂等键。

当前课程创建时已经持久化客观练习，所以 `generate_exercise` 节点选择当前会话的练习，
不会生成一个无法和作答记录关联的临时练习。简答题评价节点已作为可复用 LLM 节点提供，
待后续加入简答题领域模型和 API 后接入主图。

## 目标规划图

```text
understand_goal
→ check_missing_information ── 信息不足 ──→ END(needs_clarification)
→ diagnostic
→ build_knowledge_graph
→ validate_graph
→ generate_plan
→ wait_for_approval ── 未批准/拒绝 ──→ END
→ persist_plan
→ END(completed)
```

知识图先通过 Pydantic 输出 Schema，再经过领域层的引用、重复边和有向无环校验。计划必须
覆盖所有知识点且满足前置依赖顺序。批准前不写入知识点、边、计划或练习；批准后由一个
Application Service 在同一事务中保存经过校验的提案。

## 代码入口

- `app.agent.graphs.build_daily_learning_graph`
- `app.agent.graphs.build_goal_planning_graph`
- `app.agent.graphs.DailyLearningContext`
- `app.agent.graphs.GoalPlanningContext`

Graph 输入只需要业务 ID 和本轮用户输入。数据库 Unit of Work、模型客户端及其密钥始终
留在 Runtime Context 中。
