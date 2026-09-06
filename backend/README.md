# Backend

LearnLoop 的 FastAPI 后端、领域逻辑和本地基础设施。当前版本已实现不调用 LLM 的
垂直学习闭环；`app/agent/` 将在后续阶段接入 LangGraph。

```text
app/api/             HTTP 与 SSE 接口
app/application/     用例编排和事务边界
app/agent/           Graph、节点、工具、状态和 Prompt
app/domain/          与框架无关的学习领域逻辑
app/infrastructure/  SQLite、文件、模型和解析器实现
app/workers/         SQLite 后台任务 Worker
migrations/          Alembic 数据库迁移
evals/               Agent 评测数据、评测器和报告
tests/               后端测试
```

数据库迁移由仓库根目录下的跨平台脚本执行：

```text
uv run --project backend python scripts/migrate.py upgrade
uv run --project backend python scripts/migrate.py current
uv run --project backend python scripts/migrate.py downgrade -1
```

迁移不会在 API 启动时隐式执行，避免应用启动过程修改未知版本的数据库。

## 当前 API 能力

- 创建和查询学习目标。
- 为目标生成确定性的知识图、学习计划与练习。
- 创建和查询学习会话。
- 提交选择题答案并在同一事务内更新掌握度和 FSRS 复习计划。
- 完成学习会话并查询到期复习项。
- 通过 `Idempotency-Key` 避免目标、会话和作答被重复创建。

启动后访问 <http://127.0.0.1:8000/docs> 查看 OpenAPI 文档。
