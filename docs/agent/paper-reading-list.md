# Agent 论文与工业实践阅读清单

本文按照“读完即可逐步改造一个 Agent 项目”的依赖顺序组织，而不是按照论文发表时间排序。

配套文档：

- [Agent 主要研究方向](research-and-industry-directions.md)
- [LearnLoop 应用设计](learnloop-application-design.md)
- [LearnLoop 第二阶段路线图](phase-two-roadmap.md)

## 第一组：建立 Agent Loop 与 Planning 基础

### 1. ReAct: Synergizing Reasoning and Acting in Language Models

- 论文：[arXiv:2210.03629](https://arxiv.org/abs/2210.03629)
- 重点：Reasoning 和 Acting 如何在环境反馈中交替进行。
- 阅读问题：
  - Agent 与一次性 Chain-of-Thought 有什么区别？
  - Observation 如何改变下一步 Action？
  - 如何定义动作空间和结束条件？
- 实现练习：实现带最大步数的 `observe → action → tool → observe` 循环，不记录隐藏思维，只记录结构化动作和公开摘要。

### 2. Plan-and-Solve Prompting

- 论文：[arXiv:2305.04091](https://arxiv.org/abs/2305.04091)
- 重点：为什么先拆解任务再执行可以减少遗漏步骤。
- 阅读问题：
  - Plan 和执行结果应该如何分离？
  - 哪些计划错误可以在执行前检测？
- 实现练习：定义包含依赖、状态和成功标准的结构化 Plan。

### 3. Tree of Thoughts

- 论文：[arXiv:2305.10601](https://arxiv.org/abs/2305.10601)
- 重点：候选路径生成、状态评价、搜索和回溯。
- 阅读问题：
  - 单路径推理在哪些任务上容易失败？
  - BFS、DFS 和 Beam Search 的成本有什么区别？
- 实现练习：为同一任务生成三个候选计划，用独立 Rubric 选择一个，不必立即实现完整树搜索。

### 4. Language Agent Tree Search

- 论文：[arXiv:2310.04406](https://arxiv.org/abs/2310.04406)
- 重点：如何统一规划、行动、环境反馈、自我反思和树搜索。
- 阅读问题：
  - 环境反馈怎样成为价值估计的一部分？
  - 搜索开销何时值得？
- 实现练习：只在高难度任务上启用候选分支，并设置分支数、深度和 Token 预算。

## 第二组：理解 Tool Use 和 Agent-Computer Interface

### 5. Toolformer

- 论文：[arXiv:2302.04761](https://arxiv.org/abs/2302.04761)
- 重点：模型如何学习何时调用工具、调用什么工具以及如何使用返回结果。
- 注意：Toolformer 是训练方法，不等同于 API Function Calling；学习其决策问题即可，不必在应用阶段复现训练。
- 实现练习：建立 Tool 选择评测集，测量工具名称、参数和调用时机。

### 6. SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering

- 论文：[arXiv:2405.15793](https://arxiv.org/abs/2405.15793)
- 重点：工具接口设计怎样改变 Agent 行为和任务成功率。
- 阅读问题：
  - 为什么同一模型面对不同工具接口会表现不同？
  - 哪些格式对模型是额外负担？
- 实现练习：为同一 Tool 设计两种 Schema，用真实任务比较调用成功率和 Token。

### 工业实践补充

- [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Model Context Protocol Architecture](https://modelcontextprotocol.io/specification/2025-06-18/architecture)
- [A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)

阅读时重点关注工具命名、边界、返回值压缩、权限、风险等级、审批和评测，而不是只学习框架 API。

## 第三组：Context 与 Memory

### 7. Generative Agents

- 论文：[arXiv:2304.03442](https://arxiv.org/abs/2304.03442)
- 重点：Memory Stream、检索、Reflection 和 Planning 如何组合。
- 阅读问题：
  - Recency、Importance、Relevance 如何共同决定召回？
  - 原始事件如何形成高层 Reflection？
- 实现练习：为事件增加时间、重要度、相关度、来源和置信度。

### 8. MemGPT

- 论文：[arXiv:2310.08560](https://arxiv.org/abs/2310.08560)
- 重点：将 Context Window 视为主存、外部存储视为磁盘的虚拟上下文管理。
- 阅读问题：
  - 哪些内容常驻 Context？
  - 谁决定换入和换出？
  - Context Compaction 与 Memory Retrieval 有何区别？
- 实现练习：实现一个 Token Budget Context Builder，并保存可重新读取内容的引用 ID。

### 9. Voyager

- 论文：[arXiv:2305.16291](https://arxiv.org/abs/2305.16291)
- 重点：自动课程、环境反馈、迭代改进和可组合 Skill Library。
- 阅读问题：
  - Episodic Memory 和 Skill 有什么区别？
  - 如何判断一个成功轨迹可以抽象为通用技能？
- 实现练习：从多条成功轨迹提取候选 Skill，并用未见任务测试是否能复用。

### 10. LongMemEval

- 论文：[arXiv:2410.10813](https://arxiv.org/abs/2410.10813)
- 重点：长期记忆的提取、跨 Session 推理、时间推理、知识更新和拒答。
- 阅读问题：
  - 长 Context 为什么不等于可靠 Memory？
  - 用户更新事实后如何避免召回旧信息？
- 实现练习：构造跨多个 Session 的事实更新、冲突和无答案用例。

### 工业实践补充

- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

## 第四组：Reflection 与 Agentic RAG

### 11. Reflexion

- 论文：[arXiv:2303.11366](https://arxiv.org/abs/2303.11366)
- 重点：将外部反馈转化为语言经验，在后续 Episode 中复用。
- 阅读问题：
  - Reflection 使用的反馈来自哪里？
  - 如果没有外部验证，自我反思为什么可能重复错误？
- 实现练习：只有在测试、标准答案或用户反馈存在时才生成 Reflection。

### 12. IRCoT

- 论文：[arXiv:2212.10509](https://arxiv.org/abs/2212.10509)
- 重点：多步问题中，已获得的信息如何改变下一次检索。
- 实现练习：实现有最大轮数的“检索—发现缺口—改写 Query”循环。

### 13. CRAG

- 论文：[arXiv:2401.15884](https://arxiv.org/abs/2401.15884)
- 重点：检索结果质量不足时的纠正策略。
- 实现练习：增加证据相关性和覆盖度评价，低分时禁止直接生成最终答案。

### 14. Self-RAG

- 论文：[arXiv:2310.11511](https://arxiv.org/abs/2310.11511)
- 重点：按需检索以及对证据相关性、支持度和生成质量的反思。
- 注意：原论文包含训练 Reflection Token；API 项目可以先借鉴其控制流程和评价维度，而不是声称复现 Self-RAG。

### 15. Adaptive-RAG

- 论文：[arXiv:2403.14403](https://arxiv.org/abs/2403.14403)
- 重点：根据问题复杂度选择无检索、单步检索或多步检索。
- 实现练习：比较统一多步检索与复杂度路由在准确率、Token 和延迟上的差异。

## 第五组：Subagent 与 Multi-agent

### 16. AutoGen

- 论文：[arXiv:2308.08155](https://arxiv.org/abs/2308.08155)
- 重点：如何用可编程对话组织人、模型和工具。
- 阅读问题：
  - Conversation Pattern 如何决定协作流程？
  - 哪些状态应该进入消息，哪些应保存在外部？

### 17. MetaGPT

- 论文：[arXiv:2308.00352](https://arxiv.org/abs/2308.00352)
- 重点：通过 SOP、角色分工和中间 Artifact 降低级联错误。
- 阅读问题：角色本身是否提供能力，还是角色对应的 Context、工具和验收标准提供能力？

### 18. CAMEL

- 论文：[arXiv:2303.17760](https://arxiv.org/abs/2303.17760)
- 重点：角色扮演、任务指定和 Agent 间通信。
- 阅读问题：自由对话如何保持目标一致并可靠终止？

### 工业实践重点阅读

- [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)
- [OpenAI Agents building guide](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)

阅读这些实践时重点关注：

- Orchestrator 如何决定是否委派；
- 子 Agent 输入是否明确包含目标、范围、工具和输出格式；
- 如何控制子 Agent 数量与预算；
- 如何隔离 Context；
- 如何使用 Artifact 交接；
- 如何评价最终状态而不是固定执行路径。

## 第六组：Agent Evaluation 与安全

### 19. τ-bench

- 论文：[arXiv:2406.12045](https://arxiv.org/abs/2406.12045)
- 重点：Tool-Agent-User 交互、最终数据库状态和 `pass^k` 稳定性。
- 实现练习：同一任务重复运行多次，同时报告平均成功率和连续可靠完成概率。

### 20. GAIA

- 论文：[arXiv:2311.12983](https://arxiv.org/abs/2311.12983)
- 重点：综合推理、工具、浏览和多模态任务。
- 用途：了解通用 Agent Benchmark 如何设计明确、可验证的最终答案。

### 21. WebArena

- 论文：[arXiv:2307.13854](https://arxiv.org/abs/2307.13854)
- 重点：可复现、状态化网站环境中的长程任务和功能正确性。

### 22. OSWorld

- 论文：[arXiv:2404.07972](https://arxiv.org/abs/2404.07972)
- 重点：真实操作系统、跨应用任务、视觉定位和执行式评价。

### 23. AgentDojo

- 论文：[arXiv:2406.13352](https://arxiv.org/abs/2406.13352)
- 重点：Tool 返回的不可信内容如何通过间接 Prompt Injection 劫持 Agent。
- 实现练习：在检索资料中嵌入恶意指令，检查 Agent 是否越权调用写工具。

### 24. AI Agents That Matter

- 论文：[arXiv:2407.01502](https://arxiv.org/abs/2407.01502)
- 重点：评测污染、过拟合、可复现性，以及准确率与成本的联合评价。
- 阅读问题：一个更复杂、更贵但只提升少量成功率的 Agent 是否真的更好？

## 第七组：Agent Learning

### 25. Agent Lightning

- 论文：[arXiv:2508.03680](https://arxiv.org/abs/2508.03680)
- 重点：将任意 Agent Harness 的模型调用和环境反馈转换成可用于强化学习的轨迹。
- 阅读前置：先完成动态 Loop、Trace、Verifier、Reward 和离线评测。
- 阅读问题：
  - 多步轨迹如何进行 Credit Assignment？
  - 为什么训练系统要与 Agent Harness 解耦？
  - Reward 如何防止 Agent 投机？
- 实现练习：先把现有 Trace 转成 `state/action/observation/reward` 离线数据，不要直接启动 RL 训练。

## 建议阅读节奏

### 第一轮：建立全景

阅读摘要、架构图、方法和实验结论：

```text
ReAct
→ SWE-agent
→ Generative Agents
→ MemGPT
→ Anthropic Building Effective Agents
```

目标是形成 Loop、Tool、Context、Memory、Evaluation 的整体认识。

### 第二轮：跟随项目实现

```text
Plan-and-Solve
→ Context Engineering
→ LongMemEval
→ IRCoT / CRAG
→ Multi-agent Research System
→ τ-bench
```

每读一组，先实现最小机制和评测，再决定是否保留。

### 第三轮：研究高级方法

```text
Tree of Thoughts / LATS
→ Voyager
→ Self-RAG
→ AutoGen / MetaGPT / CAMEL
→ AgentDojo
→ Agent Lightning
```

这一轮重点是做对照实验和消融，而不是简单复刻论文名称。

## 阅读记录模板

每篇论文建议回答：

```markdown
## 论文名称

- 它解决的具体失败是什么？
- Baseline 是什么？
- 核心机制是什么？
- 哪些部分需要训练，哪些只需要推理时 Harness？
- 使用了什么环境、数据集和指标？
- 成本增加多少？
- 结果能否迁移到学习 Agent？
- 最小可实现版本是什么？
- 应该设计什么对照实验？
- 哪些结论仍缺少证据？
```

## 总结

推荐顺序可以压缩为：

```text
ReAct
→ Planning
→ Tool/ACI
→ Context
→ Memory
→ Agentic RAG
→ Subagent
→ Evaluation/Safety
→ Agent Learning
```

不要从 Multi-agent 框架或强化学习开始。先建立一个能动态行动、能被环境验证、能稳定终止的单 Agent，后续论文中的高级机制才有可靠落点。
