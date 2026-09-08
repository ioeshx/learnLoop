# 阶段十：评测、可观测性与交付完善

阶段十把 LearnLoop 从“功能可运行”推进到“质量可度量、运行可解释、结果可回归”。
这一阶段不记录模型隐藏思维过程，只记录可公开、可审计的输入元数据、结果摘要和运行指标。

## 1. Agent Trace

Agent 运行数据和 LangGraph Checkpoint 一起保存在 `data/db/checkpoints.db`。运行时维护四类
可观测数据：

| 数据 | 用途 |
| --- | --- |
| `learnloop_agent_runs` | Graph、业务资源、Thread、状态和起止时间 |
| `learnloop_agent_events` | 节点开始/完成、Interrupt、Tool、模型及运行结果事件 |
| `learnloop_tool_calls` | Tool 名称、安全参数、结果结构摘要、状态、耗时和错误 |
| `learnloop_prompt_versions` / `learnloop_model_calls` | Prompt/模型版本、Token、修复次数、耗时和错误 |

Tool 只保存参数和结果的结构摘要，例如对象键名和集合长度，不保存整篇检索内容。模型调用
只保存 Prompt 名称与版本，不保存完整 Prompt、模型响应或隐藏推理。`ContextVar` 将当前
`run_id` 传递到结构化模型边界，避免把可观测性字段混入 Agent State。

查询接口：

- `GET /api/v1/agent/runs`：最近的 Agent 运行。
- `GET /api/v1/agent/runs/{run_id}/trace`：事件、Tool、模型和汇总指标。
- `GET /api/v1/agent/prompts`：运行中实际出现过的 Prompt 版本。
- `GET /api/v1/agent/runs/{run_id}/events`：按序号重放事件。

前端 `/dashboard` 展示最近运行，点击后进入 `/agent-runs/{run_id}` 检查节点、Tool、
Prompt 版本、Token 和耗时。

## 2. HTTP 与运行日志

后端日志使用单行 JSON。每个 HTTP 请求返回 `X-Request-ID` 和 `Server-Timing`，同时记录：

- 请求 ID、方法、路径、状态码和耗时；
- Agent Run ID、Graph、状态和总耗时；
- 异常类型与栈信息。

日志字段采用白名单输出，避免将请求正文、密钥、模型原文或用户学习资料意外写入日志。
Trace 用于单次运行诊断，JSON 日志用于跨请求筛选，两者通过 `run_id` 或 `request_id`
关联。

## 3. 离线评测

第一版数据集位于 `backend/evals/datasets/v1.json`，数据集版本和阈值均进入版本控制。
当前评测器完全离线、确定性执行，不调用真实模型，因此可以稳定地作为 CI 质量门禁。

覆盖指标：

1. 结构化输出有效率。
2. Tool 选择和参数正确率。
3. Checkpoint/Interrupt 恢复率。
4. RAG Recall@K 和 MRR。
5. 引用支持准确率。
6. 客观题评分正确率。
7. 简答题评分一致性。
8. 补救路径成功率。
9. 模型 P95 延迟、平均 Token 和平均费用。

运行评测：

```bash
# 使用 uv，在项目根目录执行
uv run --project backend python scripts/run_evals.py

# 不使用 uv，先激活 backend/.venv
python scripts/run_evals.py
```

报告写入 `backend/evals/reports/latest.json`。本地报告含运行时间，不提交 Git；若任一指标
未达到数据集阈值，命令以非零状态退出。单元测试还会验证指标名称、阈值判定和固定时间下
的报告结构。

当前数据集是工程基线，不等同于真实学习效果研究。后续应持续加入人工标注案例，尤其是
长文档检索、歧义答案、错误引用、跨进程恢复和多轮补救案例；修改 Prompt、检索策略或
模型版本时，必须先运行离线评测并比较指标变化。

## 4. 学习洞察与周报

`GET /api/v1/goals/{goal_id}/insights` 从领域事实实时投影：

- 知识节点和依赖边；
- 每个节点的当前掌握度与计划状态；
- 按时间排序的掌握度事件趋势；
- 最近七天作答数、正确数、完成项和平均掌握度。

Dashboard 直接消费该读模型。周报后台任务仍负责周期性生成快照；Dashboard 的七日摘要
不依赖 Worker，因此即使后台任务尚未运行也能显示最新事实。

## 5. PWA 与本地数据

前端提供 Web App Manifest、图标、Service Worker 和浏览器安装提示。Service Worker
只缓存应用壳；API 仍采用网络优先，避免把过期的学习状态当作最新数据。

个人资料通过计划页导入，支持文件和网页。`GET /api/v1/data/export` 生成不含密钥和
完整模型 Prompt 的 JSON 学习数据备份，包含目标、知识图、计划、练习、作答、掌握度与
复习计划。第一版导出用于备份、审计和迁移准备；自动覆盖式恢复暂不开放，以免在运行中的
SQLite 数据库里误覆盖现有学习记录。

## 6. 验收命令

```bash
cd backend
.venv/bin/ruff check app evals tests ../scripts/run_evals.py \
  --extend-exclude app/infrastructure/database/types.py
.venv/bin/mypy app evals ../scripts/run_evals.py \
  --exclude app/infrastructure/database/types.py
.venv/bin/pytest
.venv/bin/python ../scripts/run_evals.py

cd ../frontend
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

测试覆盖 Trace 持久化、模型观测、Checkpoint 恢复后的 Tool Trace、Dashboard 读模型、
数据导出、离线指标，以及原有学习、RAG、后台任务和自适应复习回归。
