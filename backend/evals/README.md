# Agent Evaluations

- `datasets/v1.json`：版本化样本和质量门禁阈值。
- `evaluators/core.py`：无网络、确定性的指标实现。
- `runner.py`：加载数据集、生成报告并汇总门禁结果。
- `reports/`：本地生成的 JSON 报告；报告不提交 Git。

在仓库根目录运行：

```bash
uv run --project backend python scripts/run_evals.py
```

未使用 uv 时，激活 `backend/.venv` 后运行 `python scripts/run_evals.py`。任一指标未达到
数据集内的阈值时，进程返回非零退出码。
