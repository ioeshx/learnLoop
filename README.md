# LearnLoop

LearnLoop 是一个本地优先的自适应学习 Agent。

当前仓库处于基础架构阶段，仅建立项目目录、模块边界和运行时数据约定，暂未包含具体业务实现。

## 技术方向

- 前端：Next.js、React、TypeScript、PWA
- 后端：Python、FastAPI、Pydantic
- Agent：LangGraph、LangChain
- 数据库：SQLite
- 文件存储：本地目录，通过接口隔离
- 后台任务：Python Worker + SQLite Job 表

## 目录

```text
frontend/   Web/PWA 客户端
backend/    API、Agent、领域逻辑和基础设施
data/       本地运行时数据，不提交真实内容
scripts/    跨平台开发和维护脚本
docs/       架构、决策、API 和评测文档
```

