# LearnLoop 第二阶段高级 Agent 开发路线图

## 1. 总体目标

第二阶段不以增加页面、业务接口或固定 LangGraph 节点为主要目标，而是建立高级 Agent 内核：

```text
目标
→ 动态计划
→ 自主选择工具
→ 根据环境反馈调整
→ 管理有限上下文
→ 跨 Session 积累记忆
→ 必要时委派子 Agent
→ 使用验证结果持续改进
```

实施原则：

- 先单 Agent，后 Multi-agent；
- 先可验证执行，后自我反思；
- 先上下文和 Memory 策略，后更换存储技术；
- 先离线评测和轨迹，后强化学习；
- 每增加一层复杂度，都必须用任务成功率、稳定性和成本证明收益。

相关设计：

- [主要研究方向](research-and-industry-directions.md)
- [LearnLoop 应用设计](learnloop-application-design.md)
- [第二阶段详细实施计划](phase-two-implementation-plan.md)
- [Stage 11 Dynamic Agent Kernel 实现说明](stage-11-dynamic-agent-kernel.md)
- [Stage 12 Context Engine 实现说明](stage-12-context-engine.md)
- [推荐论文阅读清单](paper-reading-list.md)

本路线图描述能力顺序和阶段目标；具体里程碑、代码改动、迁移策略、测试门禁和首批 Backlog
见《第二阶段详细实施计划》。实施时以详细计划中的阶段 10.5、11A～11D 为起点，不直接删除或
改写现有固定图。

## 2. 开始前：建立第二阶段基线

在修改 Agent 架构前，先建立真实任务集，否则无法证明新架构优于第一阶段。

### 任务集

至少覆盖：

- 信息充分和信息不足的学习目标；
- 需要一个或多个先修知识的目标；
- 简单事实题和多跳知识题；
- 资料检索成功、召回不足和错误资料；
- 用户中途改变约束；
- 连续答错后的补救；
- Interrupt 后跨进程恢复；
- Tool 暂时失败和永久失败；
- 长 Session Context 超限；
- 恶意资料中的 Prompt Injection。

### 核心指标

- 任务最终状态成功率；
- `pass^k` 重复运行稳定性；
- Plan Step 完成率；
- Tool 选择和参数正确率；
- 无效或重复 Tool 调用数；
- 检索 Recall、MRR、证据覆盖和引用支持率；
- Memory 写入准确率和召回准确率；
- 总 Token、延迟和费用；
- 人工接管率；
- 越权或危险动作率。

## 3. 阶段 11：动态单 Agent 内核

### 目标

把模型从固定工作流中的生成器升级为受控动作空间中的决策者。

### 实现内容

1. 新增结构化 `AgentPlan` 和 `PlanStep`。
2. 实现 Planner、Executor、Verifier 和 Replanner。
3. 建立通用循环：

   ```text
   observe → decide → act → verify → replan/continue/finish
   ```

4. 将现有应用服务封装成第一批 Agent Tools。
5. 为每个 Run 设置步数、时间、Token、Tool 和费用预算。
6. 加入重复动作检测、失败阈值、取消和人工接管。
7. 将 Plan、Action、Observation 和 Verification 写入现有 Trace。

### 暂不实现

- 子 Agent；
- 长期 Memory；
- Tree Search；
- 强化学习；
- 自由形式多 Agent 对话。

### 验收标准

- 同一任务可以因环境反馈走不同合法路径；
- Tool 由模型动态选择，而不是图节点写死；
- 每一步都关联 Plan Step 和成功标准；
- Tool 失败后能重试、换方案或请求人工帮助；
- 所有运行都能在预算内终止；
- 相比第一阶段，目标任务成功率提升且成本可接受。

## 4. 阶段 12：Context Engine

### 目标

统一管理每次模型调用的 Context，避免节点各自拼接 Prompt 和历史。

### 实现内容

1. 建立 `ContextBuilder` / `ContextCompiler`。
2. 定义上下文分区和优先级。
3. 实现 Token 预算和预留输出预算。
4. 根据当前 Plan Step 动态选择 Tool Schema。
5. 实现 Just-in-time 资料加载。
6. 实现消息、Tool 结果和已完成步骤的 Compaction。
7. 保存 Context Snapshot 元数据和来源引用。
8. 增加 Context 去重、过期信息清理和冲突提示。

### 验收标准

- 长任务跨越多个 Context Window 后仍能继续；
- 压缩前后关键未完成事项和证据引用不丢失；
- 相比“全部塞入 Context”，Token 明显下降；
- Tool 选择准确率不因动态裁剪而下降；
- 可以解释某次模型调用使用了哪些上下文来源。

## 5. 阶段 13：Agent Memory

### 目标

让 Agent 跨 Session 使用可靠经验，而不是仅恢复同一个 Run。

### 实现内容

1. 区分 Working、Episodic、Semantic 和 Procedural Memory。
2. 建立候选记忆提取器。
3. 建立证据、置信度、来源、时间和有效期模型。
4. 实现去重、合并、冲突、替代和遗忘策略。
5. 实现 Memory 检索和重排。
6. 高影响用户画像变更加入审批。
7. 防止不可信 Tool 内容直接成为永久记忆。
8. 建立 LongMemEval 风格的项目内评测集。

### 验收标准

- 能正确回答跨 Session 的用户事实和学习经历；
- 用户更新偏好后不会继续使用过期事实；
- 无相关记忆时能够拒答，而不是伪造；
- Memory 召回对任务成功率有可测提升；
- 错误信息、指令注入和低置信度推断不会自动永久化。

## 6. 阶段 14：Agentic RAG 与 Research Tutor

### 目标

把固定单次混合检索升级为由证据质量驱动的多步研究循环。

### 实现内容

1. 判断问题是否需要检索。
2. 根据问题复杂度选择无检索、单步或多步检索。
3. 分解多跳问题并生成多个 Query。
4. 评价相关性、覆盖度、来源质量和重复度。
5. 证据不足时改写 Query、扩展来源或请求用户提供材料。
6. 增加 Citation Verifier。
7. 为检索设置最大轮数、来源数和 Token 预算。
8. 保存 Query、Chunk ID、证据评价和最终引用关系。

### 验收标准

- 多跳问题的 Recall 和答案正确率优于单次检索；
- 低质量检索不会直接进入最终答案；
- 重要结论都有可追踪证据；
- 不需要检索的问题不会产生无效检索成本；
- 达不到证据标准时会明确说明不足。

## 7. 阶段 15：Subagent-as-Tool

### 目标

通过独立上下文和受控委派扩大 Agent 的并行搜索和专业处理能力。

### 实现内容

1. 保留一个 Lead Learning Agent。
2. 建立 Researcher、Curriculum、Tutor、Evaluator 四类 Subagent。
3. 定义委派输入输出 Schema、Tool 白名单和预算。
4. 建立父子 Run 关系和取消传播。
5. 子 Agent 只返回压缩结果、证据和未解决问题。
6. 第一版串行委派，稳定后加入独立任务并行。
7. 增加重复任务检测和结果合并策略。
8. 对多 Agent 与同预算单 Agent 做对照评测。

### 验收标准

- 子 Agent 不共享不必要的完整上下文；
- 委派目标、范围和结果可追踪；
- 不会为简单任务创建大量子 Agent；
- 并行任务的墙钟时间得到改善；
- 多 Agent 提升任务质量时，其 Token 成本在预定范围内。

## 8. 阶段 16：Reflection 与 Skill Library

### 目标

让系统把经过验证的成功和失败轨迹转化为可复用经验。

### 实现内容

1. 建立确定性 Verifier 优先的反馈管道。
2. 失败后生成结构化 Reflection。
3. Reflection 只能引用真实 Observation 和验证结果。
4. 从重复成功轨迹提取候选 Skill。
5. 定义 Skill 的适用条件、步骤、工具和验证器。
6. 记录 Skill 来源、版本、成功率和成本。
7. 对低成功率或过期 Skill 降权、停用或重新验证。
8. 建立使用 Skill 与不使用 Skill 的消融评测。

### 验收标准

- 相似失败不会在后续任务中高频重复；
- Skill 只在适用条件满足时被召回；
- Skill 能降低调用次数或提高成功率；
- Reflection 不会覆盖领域事实或制造无证据记忆。

## 9. 阶段 17：策略优化与 Agentic RL

### 前置条件

只有同时满足以下条件才进入本阶段：

- 已有足量真实且脱敏的 Agent 轨迹；
- Reward 与最终状态高度相关；
- 存在稳定训练、验证和保留测试集；
- 可以离线重放环境或使用隔离沙箱；
- 具备 Prompt、Harness 与模型版本追踪；
- 安全和成本预算明确。

### 实现顺序

1. 离线轨迹分析和失败聚类；
2. Prompt 与 Tool 描述自动优化；
3. Context Policy A/B 测试；
4. 教学策略 Contextual Bandit；
5. 高质量成功轨迹 SFT；
6. 偏好优化；
7. 使用 Agent Lightning 类接口研究多步 Agentic RL。

### Reward 设计

Reward 必须同时包含：

- 学习目标完成度；
- 即时正确率；
- 延迟保持和迁移表现；
- 用户反馈；
- Tool、Token、延迟和费用；
- 违规、越权、泄露答案和无证据结论惩罚。

### 验收标准

- 在未见任务上提升，而不是只记住训练集；
- 成功率提升不以安全性或成本失控为代价；
- 能通过消融说明提升来自模型训练、Prompt、Context 还是 Harness；
- 所有训练结果可复现并可回滚。

## 10. 推荐提交粒度

每个阶段继续采用“小步可验收”的提交方式。例如阶段 11 可拆为：

1. `feat: add persistent executable agent plans`
2. `feat: expose typed learning agent tools`
3. `feat: add bounded plan-act-observe loop`
4. `feat: add verification and replanning`
5. `test: add end-state agent reliability suite`

不要在一个提交中同时引入动态 Loop、Memory、Multi-agent 和 RL，否则难以定位效果和失败原因。

## 11. 第二阶段最终形态

完成阶段 11～17 后，LearnLoop 应从固定学习工作流升级为：

> 一个能够围绕长期学习目标制定和修订计划，动态使用工具和检索证据，跨 Session 维护可靠记忆，按需委派专业子 Agent，并利用可验证结果持续优化教学策略的学习 Agent 系统。
