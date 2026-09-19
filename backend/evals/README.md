# Agent Evaluations

- `datasets/v1.json`：版本化样本和质量门禁阈值。
- `datasets/memory_v1.json`：LongMemEval 风格的跨 Session、时间更新、无答案、
  注入、删除和过期 Memory 冻结集。
- `datasets/research_v1.json`：Agentic RAG routing、多跳 Recall、Gap rewrite、
  Citation support、注入隔离和 Token 成本冻结集。
- `datasets/delegation_v1.json`：Subagent routing、scope isolation、取消传播、
  duplicate prevention、fallback 和同预算成本冻结集。
- `datasets/skills_v1.json`：Reflection provenance、人工审核、scope-safe recall、
  degradation、版本回滚和 Skill/no-Skill ablation 冻结集。
- `datasets/optimization_v1.json`：Reward hard gate、delayed outcome、SFT eligibility、
  holdout promotion、effective sample size、成本与 rollback 冻结集。
- `datasets/policy_v1.json`：Capability、approval、injection、secret、taint lineage、
  metadata-only audit 和 replay idempotency 冻结集。
- `datasets/model_gateway_v1.json`：Capability route、budget/deadline preflight、
  retryable fallback、repair affinity 和 circuit recovery 冻结集。
- `datasets/agent_team_v1.json`：DAG fan-out/fan-in、scope、hierarchical budget、
  cancellation、duplicate、Artifact verification 和 secret delegation 冻结集。
- `datasets/reliability_v1.json`：Seed replay、六类 Fault coverage、pass@k / pass^k、
  Recovery、Safety hard gate、成本分位数和 worst-slice 冻结集。
- `evaluators/core.py`：无网络、确定性的指标实现。
- `runner.py`：加载数据集、生成报告并汇总门禁结果。
- `reports/`：本地生成的 JSON 报告；报告不提交 Git。

在仓库根目录运行：

```bash
uv run --project backend python scripts/run_evals.py
```

未使用 uv 时，激活 `backend/.venv` 后运行 `python scripts/run_evals.py`。任一指标未达到
数据集内的阈值时，进程返回非零退出码。

Memory 专项评测：

```bash
uv run --project backend python scripts/run_evals.py \
  --dataset backend/evals/datasets/memory_v1.json \
  --output backend/evals/reports/memory-latest.json
```

Research 专项评测：

```bash
uv run --project backend python scripts/run_evals.py \
  --dataset backend/evals/datasets/research_v1.json \
  --output backend/evals/reports/research-latest.json
```

Subagent-as-Tool 专项评测：

```bash
uv run --project backend python scripts/run_evals.py \
  --dataset backend/evals/datasets/delegation_v1.json \
  --output backend/evals/reports/delegation-latest.json
```

Reflection / Skill Library 专项评测：

```bash
uv run --project backend python scripts/run_evals.py \
  --dataset backend/evals/datasets/skills_v1.json \
  --output backend/evals/reports/skills-latest.json
```

Policy optimization / Agentic RL 专项评测：

```bash
uv run --project backend python scripts/run_evals.py \
  --dataset backend/evals/datasets/optimization_v1.json \
  --output backend/evals/reports/optimization-latest.json
```

Agent Trust / Policy Engine 专项评测：

```bash
uv run --project backend python scripts/run_evals.py \
  --dataset backend/evals/datasets/policy_v1.json \
  --output backend/evals/reports/policy-latest.json
```

Model Gateway 专项评测：

```bash
uv run --project backend python scripts/run_evals.py \
  --dataset backend/evals/datasets/model_gateway_v1.json \
  --output backend/evals/reports/model-gateway-latest.json
```

Agent Team 专项评测：

```bash
uv run --project backend python scripts/run_evals.py \
  --dataset backend/evals/datasets/agent_team_v1.json \
  --output backend/evals/reports/agent-team-latest.json
```

Agent Reliability 专项评测：

```bash
uv run --project backend python scripts/run_evals.py \
  --dataset backend/evals/datasets/reliability_v1.json \
  --output backend/evals/reports/reliability-latest.json
```
