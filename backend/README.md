# Backend

FastAPI 后端、LangGraph Agent、领域逻辑和本地基础设施。

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

依赖清单和应用入口将在后端初始化阶段生成。

