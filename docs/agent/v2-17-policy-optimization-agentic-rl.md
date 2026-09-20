# 阶段 17：策略优化与 Agentic RL

## 1. 阶段结论

阶段 17 完成的是一套可运行、可审计、可回滚的 **Agent Policy Optimization
control plane**，而不是宣称已经训练出一个生产级 RL 模型。

本阶段交付了：

- 基于真实 Agent Trace 的 evidence-bound failure clustering；
- 分解式、带 delayed learning outcome 的 Reward；
- Safety non-compensable hard gate；
- 只影响教学表达策略的 Contextual Bandit；
- 精确记录 propensity、支持 crash/retry 的 replay 数据；
- IPS、SNIPS、Effective Sample Size 与成本/安全门禁；
- 版本化 Policy、holdout-only promotion 和原子 rollback；
- 人工审核后的 SFT trajectory export；
- 基于成熟 Reward 的 Preference Pair；
- Agent Lightning 风格的 public transition interface；
- Policy Lab API、Trace 可观测性、前端内部面板和冻结评测集。

仓库当前没有足量真实、脱敏、带 retention/transfer 标签的生产轨迹，所以没有执行
SFT、Preference Optimization 或 multi-step RL training。`Agentic RL` 的接口和治理路径已
完成，模型训练门禁仍保持关闭。这一点是架构的安全属性，不是被隐藏的“完成项”。

## 2. 前置条件复核

阶段路线要求六个前置条件。当前状态如下：

| 前置条件 | 当前状态 | 结论 |
| --- | --- | --- |
| 真实且脱敏的 Agent trajectories | 已有结构化 Trace，真实样本量不足 | 训练未放行 |
| Reward 与 terminal outcome 相关 | 已绑定 terminal state、Verifier、delayed outcome | 基础设施完成，待真实校准 |
| train/validation/holdout | Manifest 与 frozen split 已实现 | 工程完成，待扩大数据 |
| offline replay 或 sandbox | deterministic replay/OPE 已实现 | 完成 |
| Prompt/Harness/model versioning | Prompt、Policy、Tool、code、dataset 均入 Manifest | 完成 |
| Safety 与成本预算 | hard gate、Token/Tool/latency 指标已实现 | 完成 |

因此本阶段允许启用低风险的教学策略 Bandit，也允许生成候选训练数据；不允许把当前
小型冻结 fixture 当作生产训练证据。

## 3. 总体架构

```text
                         deterministic control plane
          ┌──────────────────────────────────────────────────┐
          │ Tool allowlist · Budget · Approval · Verifier    │
          │ Safety gate · Checkpoint · Event log             │
          └──────────────────────────────────────────────────┘
                               │ cannot be expanded by Policy
                               ▼
DynamicAgentKernel ── TeachingContext ── ContextualBandit
       │                                    │
       │                          immutable PolicyVersion
       │                                    │
       └──── ContextCompiler ◀──── BanditDecision + propensity
                    │
                    ▼
              public Agent events
                    │
          ┌─────────┴───────────┐
          ▼                     ▼
 Reflection failure       terminal Reward
 clustering                    │ provisional
                                ▼
                    retention + transfer label
                                │ mature / ineligible
                 ┌──────────────┼────────────────┐
                 ▼              ▼                ▼
           LinUCB update    SFT export     Preference Pair
                 │          human review         │
                 └──────────────┬────────────────┘
                                ▼
                     versioned replay dataset
                                │
                     IPS / SNIPS / ESS / CI
                                │
                   holdout + safety + cost gates
                                ▼
                  Candidate Policy ── atomic activate
                                │
                                └──────── rollback
```

核心边界是：**Learned Policy 只能在预先声明的 Teaching Arms 中选择，不能修改
Tool permission、Plan、Budget、Verifier 或审批要求。** 即使 Policy 输出被污染，实际能力
边界仍由 Stage 11 的 deterministic kernel 强制执行。

## 4. Policy 与 Contextual Bandit

### 4.1 PolicyVersion

`PolicyVersion` 是不可变配置，记录：

- `family/version/status`；
- `algorithm = linucb`；
- `feature_schema_version`；
- `reward_version`；
- exploration 参数 `alpha/epsilon`；
- Teaching Arms 及其 `required_tools/prohibited_actions`；
- 产生候选版本的 `source_experiment_id`。

运行学习得到的 sufficient statistics 不写回 Policy JSON，而单独存储。这样可以复现“某个
版本当时选择了什么”，也可以在不篡改旧配置的情况下回滚。

内置 version 1 是静态 Bootstrap Policy。配置从 Shadow 切到 enabled 时允许启用这个
baseline；version 2 及之后的 Candidate 必须引用 `promotable` holdout experiment。

### 4.2 Teaching Arms

当前 baseline 有四个 Arms：

1. `direct_explanation`：简洁讲解后要求可验证操作；
2. `socratic_prompt`：使用有边界的 Socratic prompt；
3. `worked_example`：仅在已经观察到 failure 时可用；
4. `retrieval_grounded`：仅当 Step 允许 `research.ask` 时可用。

Arm 只向 Context 注入 `instruction` 和 `prohibited_actions`。它不能增加 Step 未声明的 Tool，
也不能绕过 Verifier。

### 4.3 Feature vector

可审计的 `TeachingContext` 使用五维向量：

```text
x = [progress, consecutive_failures, retrieval_available, write_step, intercept]
```

特征均在 `[0, 1]` 内，最后一维为 bias。这里刻意不使用原始用户文本、hidden reasoning 或
敏感 Memory，避免让在线策略成为不可解释的权限旁路。

### 4.4 LinUCB

每个 Arm 保存：

```text
A_a = I + Σ x xᵀ
b_a = Σ r x
θ_a = A_a⁻¹ b_a
score_a = θ_aᵀx + α √(xᵀA_a⁻¹x)
```

`A⁻¹` 使用小型矩阵的 Gauss-Jordan elimination。当前只有五维，直接求逆清晰且易审计；
若未来特征维度明显增加，应改用 Cholesky solve 或增量 Sherman-Morrison update。

Bandit 外层使用 deterministic epsilon-greedy。探索 bucket 来自
`sha256(run_id, decision_point_id, policy_id)`，所以同一 Plan version/Step/attempt 的
checkpoint replay 不会选择不同 Arm；新的 attempt 会产生新的 decision point，因此 failure 后
可以重新选择 `worked_example`，不会被 Step 首次选择永久锁死。
系统同时记录实际 `propensity`：

```text
P(greedy) = 1 - ε + ε / K
P(other)  = ε / K
```

这是后续 Off-Policy Evaluation 能成立的必要日志，而不是仅用于调试的字段。

### 4.5 Context 注入

选择结果通过 `ContextCompiler` 的 `teaching_strategy` partition 注入。它有固定优先级，
但不是 mandatory；Token budget 紧张时可以被裁剪，并生成可观察的
`ContextTruncation`。Context 只包含 decision id、Policy version、Arm、instruction、
prohibited actions 和 propensity，不暴露内部统计矩阵。

## 5. Reward Engine

### 5.1 两阶段生命周期

terminal Run 首先生成 `provisional` Reward。此时只知道：

- task completion；
- deterministic Verifier pass ratio；
- Token efficiency；
- Tool efficiency；
- latency efficiency；
- Safety violations。

只有 retention 与 transfer 的延迟标签到达后，Reward 才成为 `mature`。这样避免策略只学会
提高即时答题正确率，而损害长期保持和迁移。

### 5.2 分解式 Reward

当前 `learning-reward-1.0.0` 的 scalar score 是：

```text
0.20 task_completion
+ 0.15 immediate_verification
+ 0.25 delayed_retention
+ 0.20 transfer
+ 0.10 normalized_user_feedback
+ 0.04 token_efficiency
+ 0.03 tool_efficiency
+ 0.03 latency_efficiency
```

User feedback 从 `[-1, 1]` 映射到 `[0, 1]`。缺少反馈时使用中性值 `0.5`，但 retention 和
transfer 不允许缺失。权重是 versioned baseline，尚未被真实实验校准；改变权重必须产生新
Reward version，旧实验不可混算。

Efficiency 使用 bounded ratio：

```text
efficiency = clamp(1 - actual / expected_or_budget, 0, 1)
```

### 5.3 Safety hard gate

Safety 不是负权重，而是不可补偿门禁：

```text
if safety_violation:
    status = ineligible
    optimization_score = null
```

当前从 `action_rejected` 的 unavailable/permission/allowlist/outside 信号，以及 Tool/
Observation 的 `permission_denied` 提取越权证据。unsafe trajectory 即使后续提交满分 retention
和 transfer，也不会进入 Bandit、SFT、Preference 或 promotion dataset。

未来若增加 answer leakage、PII 或 unsupported conclusion classifier，它们必须作为新的
hard-gate detector 加入，不能仅在 scalar reward 中扣分。

### 5.4 Exactly-once delayed credit

Delayed outcome 可能被 HTTP retry 重复提交，也可能在 Reward 已写入、Bandit 未更新时进程
崩溃。实现以 `BanditDecision.reward_id` 作为 idempotency marker，并在一个
`BEGIN IMMEDIATE` transaction 中同时：

1. 确认 decision 尚未消费 Reward；
2. 更新 Arm 的 `A` 和 `b`；
3. 增加 observation count；
4. 绑定 decision 与 reward。

因此同一 Reward 最多学习一次；崩溃重试也能补做未完成的 credit assignment。

## 6. Failure analysis

`analyze_failures()` 只读取 Stage 16 的 failure `RunReflection`。Reflection 的每条 root cause
已经绑定 Observation/Verifier evidence，因此 clustering 不依赖 hidden Chain-of-Thought。

当前确定性算法按以下 key 聚合：

```text
(problem_category, normalized sorted root_causes)
```

输出 SHA-256 signature、Run ids、root causes 和 event references。它适合先发现 Harness、
Tool description 或 deterministic rule 的系统性问题。数据规模增大后，可在保留同样
provenance contract 的前提下增加 embedding clustering，但不能把无 evidence 的模型猜测混入。

## 7. Offline replay 与实验门禁

### 7.1 ExperimentManifest

每个实验固定：

- baseline/candidate Policy；
- dataset version 与 split；
- model version；
- Prompt versions；
- Tool Registry version；
- code version；
- Reward version；
- ESS、Reward lift、Safety 和 Token ratio 阈值。

只有 `split=holdout` 的报告可能晋升。train 和 validation 即使分数再高也只能用于开发，不能
产生 promotable report。

### 7.2 IPS 与 SNIPS

对 behavior Policy 记录的动作 `a_i`：

```text
w_i = π_candidate(a_i | x_i) / p_logged(a_i | x_i)
IPS   = (1/N) Σ w_i r_i
SNIPS = Σ w_i r_i / Σ w_i
ESS   = (Σ w_i)² / Σ w_i²
```

IPS 给出标准 importance-sampling 估计；SNIPS 用自归一化降低 variance；ESS 防止高权重的
少量样本制造虚假提升。报告同时给出 weighted standard error 的 95% interval。

当前实现没有 clipping、Doubly Robust estimator 或 sequential importance sampling。若未来
策略与 behavior policy 差异很大，应增加 weight diagnostics、clipping sensitivity 和 DR
baseline，不能仅相信单一 SNIPS 数字。

### 7.3 Promotion gates

报告同时满足以下条件才会标记 `promotable`：

- frozen holdout split；
- `ESS >= minimum_effective_sample_size`；
- Safety violation 不超过阈值，默认必须为零；
- Token ratio 不超过阈值；
- SNIPS Reward lift 达标。

Policy revision 必须引用这一 report。Activation 使用 optimistic version check，并在单个
SQLite transaction 内停用旧 Active、启用新 Candidate。数据库 partial unique index 保证同一
family 最多一个 Active。Rollback 复用同一原子切换路径，旧 Policy JSON 和统计不删除。

## 8. SFT、Preference 与 Agentic RL 接口

### 8.1 SFT curation

`export_sft()` 只导出同时满足以下条件的 Run：

- Run completed；
- Reward mature；
- Safety hard gate passed；
- 所有 Verifier passed；
- `TrajectoryReview` 是 human approved；
- 至少有一个 public action transition。

导出包含 prompt versions、Tool names、Policy reference、actions、observations、Verifier outputs
和 terminal reward。不导出 hidden Chain-of-Thought。审核被拒、只有即时指标、存在失败
Verifier 或 unsafe 的轨迹都会被排除。

### 8.2 Preference Pair

Preference Pair 要求 chosen/rejected 两条轨迹都拥有 mature、safe Reward，且：

```text
margin = chosen_score - rejected_score > 0
```

Pair 记录相同任务上下文的 fingerprint、两个 Reward ids、margin 和人工 evidence note。当前
实现保证分数方向正确，但在生产数据中还应增加 rubric 一致性、长度偏差检查和双人审核。

### 8.3 Agent Lightning-style transition

统一训练接口采用：

```text
Transition(state_ref, public action, observation, verifier, reward)
```

`state_ref` 是可回放 Run/event reference，不复制整个敏感 Context。当前 credit assignment 是
保守 baseline：中间 transition reward 为 `0`，成熟 terminal Reward 赋给最后 action。这足以
让外部 training harness 消费同一种结构，但还不是完整的 multi-step RL 算法。

未来接入 Agent Lightning、PPO/GRPO 或其他 learner 前，需要额外验证：

- trajectory-level credit assignment；
- long-horizon variance；
- off-policy correction；
- action masking 与 Tool schema compatibility；
- reward hacking 与 grader drift；
- held-out learner cohort 的 retention/transfer。

## 9. 存储与可观测性

Stage 17 在 Agent checkpoint SQLite 中增加：

| 表 | 作用 |
| --- | --- |
| `learnloop_policy_versions` | immutable Policy config 与 lifecycle |
| `learnloop_bandit_statistics` | 每个 Policy/Arm 的 `A`、`b`、count |
| `learnloop_bandit_decisions` | context、Arm、score、propensity、Reward link |
| `learnloop_rewards` | decomposed Reward 与 maturity state |
| `learnloop_trajectory_reviews` | human training-data review |
| `learnloop_preference_pairs` | preference dataset provenance |
| `learnloop_policy_experiments` | Manifest、OPE report 与 promotion status |

新增事件：

- `policy_selected`；
- `reward_recorded`；
- `reward_matured`。

Agent Trace API 返回 `reward` 和 `policy_decisions`，因此可以从最终分数追溯到每一步的 Policy
version、Arm、context features 和 logging propensity。

## 10. API 与 Policy Lab

内部 API 位于 `/api/v1/agent/optimization`：

| Method | Path | 用途 |
| --- | --- | --- |
| GET | `/policies` | Policy versions |
| POST | `/policies/{id}/revisions` | 从 promotable report 创建 Candidate |
| POST | `/policies/{id}/activate` | 原子 promotion/rollback |
| GET | `/rewards` | Reward records |
| POST | `/runs/{id}/outcome` | 提交 retention/transfer |
| POST | `/runs/{id}/review` | 人工审核 training trajectory |
| GET | `/datasets/sft` | 导出合格 SFT trajectories |
| POST/GET | `/preferences` | 创建/读取 Preference Pair |
| GET | `/decisions` | Bandit decision log |
| POST | `/experiments/evaluate` | 执行 offline OPE |
| GET | `/experiments` | 实验报告 |
| GET | `/failure-clusters` | evidence-bound failure clusters |

读取 Policy/Reward/Decision 可以用于内部观测；改变 Policy、提交 outcome、审核或导出数据受
`AGENT_POLICY_ADMIN_ENABLED` 控制。Policy Lab 页面集中展示版本、mature Reward、Safety
reject、OPE/ESS 和 failure clusters，不显示 system Prompt 或 hidden reasoning。

## 11. 配置与默认行为

```dotenv
AGENT_POLICY_OPTIMIZATION_ENABLED=false
AGENT_POLICY_ADMIN_ENABLED=true
AGENT_POLICY_EXPECTED_LATENCY_MS=30000
```

`AGENT_POLICY_OPTIMIZATION_ENABLED=false` 是默认值。关闭时仍创建 Candidate baseline、记录
terminal Reward 并允许离线分析，但不向 Dynamic Agent 注入 Bandit 选择。这样可以先积累
Shadow telemetry，再显式开放在线策略。

紧急回退有两层：

1. 关闭 optimization feature flag，立即停止在线选择；
2. 将已知稳定旧 Policy 原子恢复为 Active。

## 12. 测试与冻结评测

单元/集成测试覆盖：

- disabled/active Bootstrap 行为；
- eligible Arm、Tool scope 与 retry-idempotent decision；
- terminal Reward、delayed maturity 与 exactly-once Bandit update；
- Safety violation 永久 ineligible；
- SFT 的 mature/safe/Verifier/human-review 门禁；
- Preference margin 与 mature safe provenance；
- evidence-bound failure clustering；
- IPS/SNIPS/ESS、holdout promotion 与 rollback；
- API read/admin boundary；
- 前端 version/propensity/reward/experiment 字段保持。

`optimization_v1.json` 冻结以下 gates：

- Reward hard-gate accuracy；
- delayed maturity accuracy；
- SFT eligibility accuracy；
- holdout promotion accuracy；
- rollback success；
- Safety regression rate；
- unseen/holdout Reward lift；
- Token ratio；
- Effective Sample Size。

运行：

```bash
backend/.venv/bin/python scripts/run_evals.py \
  --dataset backend/evals/datasets/optimization_v1.json \
  --output backend/evals/reports/optimization-latest.json
```

## 13. 已知限制与下一步训练门禁

工程能力已经完成，但以下事实阻止我们宣称“RL model 已完成”：

1. 当前冻结数据主要验证 contract 和 gate，不代表真实 learner distribution；
2. Reward 权重尚未通过 longitudinal study 校准；
3. OPE 的 support assumption 需要真实 propensity coverage 验证；
4. 当前只实现 terminal credit，没有训练 learner、optimizer 或 model checkpoint registry；
5. failure clustering 是确定性精确聚合，尚未覆盖语义相近但措辞不同的失败；
6. answer leakage/PII/unsupported claim 需要专用 detector 才能成为完整 hard gate；
7. 生产 promotion 仍需 canary、worst-case slice 与在线 kill-switch 演练。

实际启动 SFT/Preference/Agentic RL 前，必须补齐：

- 足量、脱敏、用户授权的真实 trajectories；
- 跨时间 retention 与新题 transfer 标签；
- learner/task/cohort 隔离的 train/validation/holdout；
- Prompt-only、Context-only、Harness-only、model-training ablation；
- reward hacking red-team 和 grader calibration；
- offline OPE 通过后的小流量 canary；
- Safety、成本和最坏切片零回归；
- model/data/code/Policy/Reward 全版本 rollback 演练。

这使 Stage 17 的状态非常明确：**Policy optimization runtime 与 Agentic RL data/control
interface 已交付；生产模型训练必须等待真实数据门禁，不会用 fixture 制造虚假收益。**
