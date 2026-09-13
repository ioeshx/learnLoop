# Agent 研究与工业实践的主要技术方向

本文梳理现代 LLM Agent 的主要研究方向和工业实践，只解释概念、机制、价值与局限，不讨论如何将这些技术应用到 LearnLoop。具体项目设计参见 [LearnLoop 高级 Agent 应用设计](learnloop-application-design.md)。

## 1. 动态 Agent Loop

### 定义

动态 Agent Loop 是指模型不只完成一次文本生成，而是在环境中反复执行“观察、决策、行动、再观察”的闭环：

```text
Observe → Decide → Act → Observe → ... → Finish
```

其中：

- **Observe**：读取用户目标、当前状态、工具结果和环境反馈。
- **Decide**：选择下一步动作，而不是沿开发者预先写死的路径执行。
- **Act**：调用工具、请求信息或产生结构化结果。
- **Finish**：在目标满足、预算耗尽或需要人工介入时结束。

[ReAct](https://arxiv.org/abs/2210.03629) 是这一方向的代表工作，它把推理与行动交替进行，使模型能够利用工具结果修正后续决策。生产系统不应依赖或持久化隐藏思维过程，而应记录可公开的计划、动作、观测和简短决策摘要。

### 核心工程问题

- 动作集合如何定义。
- 如何判断目标已经完成。
- 如何避免无限循环和重复调用。
- 工具失败后是重试、换工具、重新规划还是请求人工帮助。
- 如何约束步数、时间、Token 和费用。
- 如何确保执行具有幂等性和可恢复性。

动态循环是固定 LLM 工作流走向自主 Agent 的基础，但自主性也会带来路径不确定、错误累积和成本上升。

## 2. Planning、Execution 与 Replanning

### 定义

Planning 是把高层目标转化为可执行子目标、依赖关系和成功标准。高级 Agent 的计划不是一次性生成的自然语言清单，而是运行期间可以查询、更新、验证和重排的状态对象。

一个可执行计划通常包含：

- 总体目标；
- 子任务与依赖；
- 每步状态；
- 前置条件；
- 可用工具；
- 预期产物；
- 成功标准；
- 执行证据；
- 时间、调用次数和费用预算。

[Plan-and-Solve](https://arxiv.org/abs/2305.04091) 将“制定计划”和“执行计划”分开；[Tree of Thoughts](https://arxiv.org/abs/2305.10601) 探索多条候选路径并进行选择与回溯；[Language Agent Tree Search](https://arxiv.org/abs/2310.04406) 进一步结合环境反馈、价值判断和树搜索。

### 常见架构

```text
Planner → Executor → Verifier
   ↑                     │
   └────── Replanner ────┘
```

- **Planner**：创建或更新计划。
- **Executor**：一次只执行有限步骤。
- **Verifier**：根据外部证据检查是否达标。
- **Replanner**：在失败、环境变化或发现新信息时调整计划。

规划能力的关键不是让模型输出更多文字，而是让计划成为可验证、可回溯、可修改的运行状态。

## 3. Tool Use 与 Agent-Computer Interface

### 定义

Tool Use 研究模型如何判断：

- 是否需要工具；
- 应使用哪个工具；
- 参数如何构造；
- 如何理解工具结果；
- 工具失败后如何恢复；
- 哪些工具可以并行调用。

[Toolformer](https://arxiv.org/abs/2302.04761) 研究模型如何学习决定调用 API 的时机、种类和参数。[SWE-agent](https://arxiv.org/abs/2405.15793) 强调 Agent-Computer Interface（ACI）：模型面对的工具接口质量，会显著影响任务完成能力。

### 工业实践

工具不是简单地把传统 REST API 一比一包装给模型。优秀的 Agent 工具通常具有：

- 清晰、互不重叠的职责；
- 无歧义的名称和参数；
- 严格的输入输出 Schema；
- 高信号、低 Token 的结果；
- 标准错误分类和可恢复建议；
- 幂等键、超时和取消机制；
- 只读、可逆性、权限与风险级别元数据；
- 对敏感或不可逆操作的人工审批。

Anthropic 的工具实践建议优先设计少量面向完整任务的高价值工具，并用真实任务评测持续优化工具描述与返回格式：[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)。

[Model Context Protocol](https://modelcontextprotocol.io/specification/2025-06-18/architecture) 则为工具、资源和 Prompt 的发现与调用提供标准协议，但协议标准化不能替代工具本身的语义、权限和安全设计。

## 4. Context Engineering

### 定义

Prompt Engineering 主要研究指令如何表达；Context Engineering 研究每一次模型推理时，有限上下文窗口中应该出现哪些信息。

上下文可能包括：

- 系统规则；
- 用户目标；
- 当前计划；
- 消息历史；
- 工具定义与结果；
- 检索证据；
- 长期记忆；
- 子 Agent 产物；
- 当前环境状态。

核心目标不是尽量塞满上下文，而是选择能够提高任务成功率的最小高信号 Token 集合。长上下文仍可能出现注意力稀释、信息冲突、过期状态和成本上升。[Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) 将其视为长程 Agent 的核心工程问题。

### 常用技术

- **Token budgeting**：为指令、计划、工具、证据和历史分配预算。
- **Just-in-time loading**：只在需要时通过标识符读取材料。
- **Compaction**：把已经完成的历史压缩成结构化摘要。
- **Context pruning**：去掉重复、无关或过期信息。
- **Dynamic tool selection**：只向模型暴露当前任务可能使用的工具。
- **Structured note-taking**：把进展、决定和未完成事项保存为外部 Artifact。
- **Context isolation**：让子任务在独立上下文中运行，只返回压缩产物。

## 5. Agent Memory

### 定义

Memory 不是“保存所有对话”，而是一套写入、组织、检索、更新、冲突处理和遗忘策略。常见分类如下。

### Working Memory

当前任务立即需要的短期状态，例如当前目标、计划步骤、最近观测和临时变量。通常位于上下文、运行状态或 Checkpoint 中。

### Episodic Memory

过去发生过的具体经历，例如某次任务采取了什么行动、为什么失败、最后如何解决。

### Semantic Memory

从多次经历中提炼出的稳定事实、关系、偏好和概念，不绑定单次事件。

### Procedural Memory

可复用的做事方法、工具调用配方、程序、技能或策略。

[Generative Agents](https://arxiv.org/abs/2304.03442) 使用 Memory Stream、反思和动态检索支持长期行为；[MemGPT](https://arxiv.org/abs/2310.08560) 借鉴操作系统虚拟内存，在上下文与外部存储之间移动信息；[Voyager](https://arxiv.org/abs/2305.16291) 将成功行为保存为可检索、可组合的技能库。

### 核心难点

- 什么值得成为长期记忆；
- 如何保留来源、时间和置信度；
- 新事实与旧事实冲突时如何更新；
- 检索粒度是消息、事件、Session、事实还是技能；
- 如何避免错误信息和 Prompt Injection 被永久写入；
- 如何实现过期、衰减、合并和删除；
- 如何证明 Memory 确实改善任务，而不只是增加 Token。

[LongMemEval](https://arxiv.org/abs/2410.10813) 从信息提取、跨 Session 推理、时间推理、知识更新和拒答等维度评估长期交互记忆。

## 6. Agentic RAG

### 定义

传统 RAG 通常执行一次“查询—检索—生成”。Agentic RAG 把检索变成动态决策过程：

```text
分析问题
→ 判断是否需要检索
→ 分解问题
→ 检索
→ 评价证据
→ 发现缺口
→ 改写查询或更换来源
→ 再检索
→ 交叉验证
→ 生成带证据结果
```

代表工作包括：

- [IRCoT](https://arxiv.org/abs/2212.10509)：推理和检索交替进行。
- [CRAG](https://arxiv.org/abs/2401.15884)：先判断检索质量，质量不足时触发纠正策略。
- [Self-RAG](https://arxiv.org/abs/2310.11511)：学习何时检索，并评价证据与生成内容。
- [Adaptive-RAG](https://arxiv.org/abs/2403.14403)：根据问题复杂度选择不检索、单步检索或多步检索。

Agentic RAG 的进步主要来自检索策略、证据评价和迭代控制，而不是简单更换向量数据库。

## 7. Subagent 与 Multi-agent

### 定义

Subagent 是在独立上下文中完成受限子任务，并向上级返回结构化结果的 Agent。Multi-agent 系统则让多个 Agent 通过委派、交接、共享 Artifact 或通信协议共同完成任务。

常见模式包括：

### Orchestrator-Worker

主 Agent 负责规划、分工、预算和汇总，Worker 独立处理子任务。这是目前最实用的模式之一。

### Handoff

一个 Agent 将对话控制权转交给另一个专业 Agent，适合职责边界和工具权限明显不同的阶段。

### Peer Collaboration / Debate

多个 Agent 相互讨论、批评或投票，适合候选方案生成和交叉检查，但容易产生重复工作、共识偏差、通信膨胀和终止困难。

代表性研究包括：

- [AutoGen](https://arxiv.org/abs/2308.08155)：通过可编程 Agent 对话构建复杂应用。
- [MetaGPT](https://arxiv.org/abs/2308.00352)：用角色和标准作业流程组织多 Agent。
- [CAMEL](https://arxiv.org/abs/2303.17760)：通过角色扮演研究 Agent 协作。

Anthropic 的生产研究系统采用 Lead Agent 与并行 Subagent：子 Agent 使用独立上下文进行搜索，只向主 Agent返回压缩发现。该实践同时指出，多 Agent 更适合可并行、范围广、信息超过单一上下文的任务；对共享状态强、依赖密集的任务，协调成本可能超过收益：[How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)。

多 Agent 的价值主要来自并行搜索、上下文隔离和专业工具，而不是给同一个模型设置更多角色名称。

## 8. Reflection、Verifier 与 Evaluator

### 定义

Reflection 让 Agent 总结失败原因和改进方向；Verifier 使用规则、测试、环境状态或证据判断产物是否满足要求；Evaluator 根据 Rubric 对方案或结果评分。

[Reflexion](https://arxiv.org/abs/2303.11366) 将环境反馈转化为语言形式的经验，并放入 Episodic Memory，供后续尝试使用。

可靠的闭环应当是：

```text
执行 → 获取外部反馈 → 验证结果 → 失败归因 → 写入经验 → 重试或重新规划
```

仅让同一个模型“再检查一遍”并不可靠，因为它可能重复或合理化原来的错误。高质量评价优先使用：

- 确定性规则；
- 单元测试；
- 数据库最终状态；
- 形式验证器；
- 引用与原文对齐；
- 用户明确反馈；
- 带清晰 Rubric 的独立模型评审；
- 人工抽检。

## 9. Long-running Agent Harness

### 定义

长程 Agent 需要跨越多个上下文窗口，持续工作几十分钟、数小时甚至跨 Session。单纯扩大上下文窗口或自动摘要通常不足以维持长期一致性。

长程 Harness 通常需要：

- 持久化目标和计划；
- Checkpoint 与恢复；
- Context Compaction 和重置；
- 结构化进度日志；
- 可复用 Artifact；
- 跨 Session Handoff；
- 每轮有限增量；
- 明确完成标准；
- 超时、预算、取消和人工接管。

Anthropic 的长程 Agent 实践使用初始化 Agent 建立环境、功能清单和进度文件，后续 Agent 每次完成有限增量并留下结构化交接信息：[Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)。

另一种实践是 Planner、Generator、Evaluator 分工，以验收契约和共享文件传递状态：[Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)。

## 10. Agent Learning 与 Agentic Reinforcement Learning

### 定义

Agent Learning 研究如何利用真实任务轨迹改进模型或 Harness 策略，包括：

- Tool 选择；
- 规划和重新规划；
- Context 选择；
- Memory 写入与召回；
- 是否委派；
- 何时停止；
- Prompt 和工具描述优化。

早期可以使用规则、离线回放、Prompt 优化和 Contextual Bandit；有足够高质量轨迹和明确 Reward 后，再考虑监督微调、偏好优化或强化学习。

[Agent Lightning](https://arxiv.org/abs/2508.03680) 将 Agent 运行抽象为状态、模型动作、环境转换和奖励，并将 Agent Harness 与训练系统解耦，使复杂工具调用或多 Agent 轨迹可以用于优化。

### 主要难点

- 长轨迹中的 Credit Assignment；
- Reward 稀疏或容易被投机利用；
- 环境和任务分布不断变化；
- 线上探索的安全与成本；
- 训练时 Harness 与部署时 Harness 不一致；
- 优化成功率时损害安全、效率或可解释性。

Agentic RL 位于成熟 Agent 工程链路的后端：如果没有可信评测、执行轨迹、环境反馈和 Reward 定义，直接训练通常没有可靠目标。

## 11. 评测、安全与可观测性是横向基础

以上所有方向都依赖可观测性、评测和安全边界。高级 Agent 的执行路径并不固定，因此更适合评价最终状态、关键检查点、稳定性、成本和副作用，而不是要求每次都走相同路径。

值得参考的评测包括：

- [τ-bench](https://arxiv.org/abs/2406.12045)：通过最终数据库状态评价 Tool Agent，并用 `pass^k` 衡量重复运行稳定性。
- [GAIA](https://arxiv.org/abs/2311.12983)：综合推理、浏览、多模态和工具使用。
- [WebArena](https://arxiv.org/abs/2307.13854)：在可复现网站环境中评价长程任务。
- [OSWorld](https://arxiv.org/abs/2404.07972)：在真实操作系统和应用中评价多模态 Agent。
- [AgentDojo](https://arxiv.org/abs/2406.13352)：评价工具 Agent 面对间接 Prompt Injection 的鲁棒性。
- [AI Agents That Matter](https://arxiv.org/abs/2407.01502)：强调准确率、成本、鲁棒性和可复现性应共同报告。

工业系统还需要最小权限、沙箱、网络出口控制、危险操作审批、凭证隔离和审计。可观测性不能记录隐藏思维或无边界复制敏感数据，但必须能重建公开的计划、动作、观测、环境结果和成本。

## 总结

现代高级 Agent 的重点不是某一个框架，而是以下系统组合：

```text
Model Policy
+ Dynamic Loop
+ Executable Plan
+ Agent-oriented Tools
+ Context Management
+ Memory
+ Environment Feedback
+ Delegation
+ Evaluation and Safety
+ Learning from Trajectories
```

研究新架构时，应以任务成功率、重复运行稳定性、成本、延迟和安全边界为依据，逐步证明每一层复杂度是否真正带来收益。
