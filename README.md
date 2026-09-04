# LearnLoop

LearnLoop 是一个本地优先的自适应学习 Agent。

当前仓库正在按照 `docs/implementation-roadmap.md` 分阶段实现。

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

## 开发环境

- Python 3.12 或 3.13
- uv
- Node.js 22 或更新的 LTS/Current 版本
- Corepack 与 pnpm 11

安装依赖：

```text
cd backend
uv sync

cd ../frontend
corepack pnpm install
```

启动前后端：

```text
uv run python scripts/dev.py
```

单独启动后端或前端：

```text
uv run python scripts/dev.py --backend-only
uv run python scripts/dev.py --frontend-only
```

后端健康检查地址为 `http://127.0.0.1:8000/api/v1/health`，前端默认地址为 `http://127.0.0.1:3000`。
