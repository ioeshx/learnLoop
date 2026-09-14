# ADR-0001：v2 Dynamic Agent Kernel、存储与安全边界

- 状态：Accepted
- 日期：2026-09-14
- 范围：LearnLoop v2 Stage 11

## Context

LearnLoop v1 使用 `daily_learning` 和 `goal_planning` 两个固定 LangGraph。它们提供可靠的
Checkpoint、Interrupt、SSE 和领域写入，但模型不能根据 Observation 动态选择下一步。
Stage 11 需要引入动态决策，同时必须保留 v1 基线、回滚能力和确定性领域边界。

## Decision

1. 保留 `fixed_v1`，新增独立 `dynamic_v2`，创建 Run 时显式选择 `engine_version`；运行中
   不做隐式 Engine 切换。
2. `dynamic_v2` 采用有界 `observe → decide → act → verify → replan` Kernel，而不是把
   v1 固定节点逐个改成动态节点。
3. 模型只输出严格 Pydantic Schema 的 `AgentPlan`、`AgentAction` 和 `ReplanProposal`；
   Budget、权限、状态迁移、Tool timeout、幂等和 Verification 均由代码控制。
4. `AgentPlan` 是一次 Session 的短期执行计划，不替代面向用户的长期 `StudyPlan`。
5. 所有 Side Effect 必须通过 `ToolRegistry/ToolExecutor` 调用 Application Service；模型
   不能访问 Repository 或 SQL。
6. 默认 `LEARNLOOP_AGENT_DYNAMIC_WRITES_ENABLED=false`，即 Shadow/read-only 模式。
   受控写入通过配置显式开放，写 Tool 仍复用领域事务和幂等规则。
7. Stage 11 继续把 Run metadata、Event、Trace、Plan Version 和 dynamic durable state
   存在 `checkpoints.db`。Store 启动时执行显式 table rebuild，移除 v1 的单 Run unique
   constraint，并增加版本字段。Stage 12 引入 Artifact Store 时再拆分大体积内容。
8. Trace 默认保存公开决策、摘要、hash/keys 和 Token 统计，不保存 hidden reasoning；
   Tool 参数中的答案字段会脱敏。完整 dynamic state 只通过本地调试 API 暴露。

## Consequences

- v1 与 v2 可以针对同一 Session 创建多个 Run，支持 `pass^k` 和 paired comparison。
- 动态 Agent 即使模型输出错误，也不能突破 Plan allowlist、Tool Schema、Budget 或领域服务。
- 进程重启后可从 dynamic durable state 和 pending Interrupt 继续执行。
- Stage 11 暂未实现 Stage 12 的完整 Context compaction/Artifact、Stage 13 Memory、Stage 15
  Subagent，也不进行 RL。
- 真实生产 Canary 仍需运行 Shadow 数据集并冻结门槛；代码只提供 Engine 开关、只读默认值、
  paired comparison 和一键回退基础设施。

