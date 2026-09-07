# LearnLoop

LearnLoop 是一个本地优先、跨平台的自适应学习 Agent 项目。项目使用
Next.js 提供 Web 界面，FastAPI 提供后端 API，并使用 SQLite 在本地保存学习目标、
知识点、学习计划、练习、掌握度和复习计划。

目前已经完成开发路线中的阶段一至阶段九：工程基础、SQLite 领域模型、端到端学习
闭环、可选的 LLM 结构化内容生成、LangGraph 核心工作流、可恢复的
Checkpoint / Interrupt / SSE、本地文件与混合 RAG、SQLite 后台任务，以及可解释的
自适应学习与 FSRS 复习。用户可以
导入个人 TXT、Markdown、PDF 或单个网页；Agent 会检索相关 Chunk，并在讲解和练习中
展示可核验的页码或章节引用。

## 当前能力

- FastAPI 应用、统一配置、结构化日志和错误响应。
- Next.js、React、TypeScript 和 Tailwind CSS 前端基础工程。
- SQLite 异步访问、Alembic 迁移和 Unit of Work 事务边界。
- 学习目标、知识图、学习计划、练习、掌握度和复习领域模型。
- 知识依赖环检测、客观题确定性判分和 FSRS 复习调度。
- 事件溯源式掌握度重算、规则化难度调整和真实前置知识缺口检测。
- 按逾期、掌握度和难度排序的复习队列，以及独立复习会话和安全延期。
- 配置模型时生成自适应复习题，离线或模型失败时自动回退规则题。
- 无 LLM 的固定课程回退，以及完整的 Application Service 编排层。
- 8 个 Pydantic 结构化输出契约和 7 个带版本的 Prompt。
- 可替换的模型 Provider、Fake Provider 和 DeepSeek Provider。
- 最多一次输出修复、瞬时错误重试、超时控制和 Token 统计。
- LLM 生成的知识图、计划、讲解和练习必须经过领域规则才能持久化。
- LangGraph 每日学习图和目标规划图，以及有界的错误补救循环。
- SQLite `AsyncSqliteSaver`、稳定的 Run/Thread 映射和跨进程 Interrupt 恢复。
- 作答、评分纠正、计划批准/编辑与资料歧义确认的人机协作节点。
- 可持久化重放的节点、Tool 与运行事件，以及支持断线续传的 SSE API。
- 内容寻址的本地文件存储、SHA-256 去重、原子移动、路径安全和删除回收。
- TXT、Markdown、PDF、HTML 解析以及保留页码/章节的重叠分块。
- SQLite FTS5 BM25、NumPy 余弦相似度和 Reciprocal Rank Fusion 混合检索。
- 默认离线 Embedding，以及可选的 OpenAI-compatible `/embeddings` Provider。
- SQLite 持久化任务、原子领取、租约心跳、崩溃恢复、幂等、取消和指数退避。
- 独立 Worker 执行资料解析与 Embedding、周报聚合和到期复习快照生成。
- 创建目标、获取计划、学习/复习会话、答案纠正、复习延期和自适应解释的 REST API。
- 创建目标、学习计划、个人资料库、学习会话、作答结果和今日复习页面。
- 请求幂等、事务回滚、领域测试、API 契约测试和前端 API 测试。

## 项目目录

```text
learnLoop/
├── backend/    FastAPI、领域逻辑、SQLite、Alembic 和测试
├── frontend/   Next.js Web 客户端和前端测试
├── data/       SQLite、日志和本地文件等运行时数据
├── docs/       架构设计、未来演进和开发路线
└── scripts/    跨平台启动脚本和数据库迁移脚本
```

## 运行环境

两种运行方式都需要：

- Python 3.12 或 3.13。
- Node.js 22 或更新版本。
- Corepack 和 pnpm 11。

如果选择 uv 方式，还需要安装 [uv](https://docs.astral.sh/uv/)。如果不使用 uv，
则使用 Python 自带的 `venv` 和 `pip`。

以下命令默认在项目根目录 `learnLoop/` 中执行。

## 配置环境变量

后端读取根目录下的 `.env`，前端读取 `frontend/.env.local`。首次运行时复制示例配置：

Linux 或 macOS：

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env.local
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env.local
```

默认配置已经能够运行当前版本。主要配置如下：

```dotenv
LEARNLOOP_ENVIRONMENT=development
LEARNLOOP_DATA_DIR=./data
LEARNLOOP_LLM_PROVIDER=none
LEARNLOOP_CHECKPOINT_RETENTION_DAYS=30
LEARNLOOP_EMBEDDING_PROVIDER=local
LEARNLOOP_EMBEDDING_DIMENSIONS=384
LEARNLOOP_WORKER_LEASE_SECONDS=60
LEARNLOOP_JOB_MAX_ATTEMPTS=3
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

业务 SQLite 数据库和 RAG 索引默认创建在 `data/db/learnloop.db`，原始资料保存在
`data/files/`，Agent Checkpoint 和可重放事件保存在 `data/db/checkpoints.db`。保持
`LEARNLOOP_LLM_PROVIDER=none` 时不需要任何模型
密钥，系统使用固定课程模板；每日学习 Agent 仍可运行，目标规划 Agent 需要启用模型。

如需让 DeepSeek 生成知识图、计划、讲解和练习，在 `.env` 中配置：

```dotenv
LEARNLOOP_LLM_PROVIDER=deepseek
LEARNLOOP_LLM_MODEL=deepseek-v4-flash
LEARNLOOP_LLM_API_KEY=替换为你的密钥
LEARNLOOP_LLM_BASE_URL=https://api.deepseek.com
LEARNLOOP_LLM_TIMEOUT_SECONDS=60
LEARNLOOP_LLM_MAX_RETRIES=2
```

模型生成在创建计划时发生。已生成过计划的目标会直接返回已有计划，不会重复调用
模型。Embedding 默认使用完全离线的本地特征哈希实现。

如需使用兼容 OpenAI Embeddings 协议的远程服务，可配置：

```dotenv
LEARNLOOP_EMBEDDING_PROVIDER=openai_compatible
LEARNLOOP_EMBEDDING_MODEL=text-embedding-3-small
LEARNLOOP_EMBEDDING_API_KEY=替换为你的密钥
LEARNLOOP_EMBEDDING_BASE_URL=https://api.openai.com/v1
LEARNLOOP_EMBEDDING_DIMENSIONS=384
```

使用远程 Embedding 时，导入的 Chunk 和检索文本会发送给配置的服务。保持 `local` 时
所有资料和检索均留在本机。

## 方式一：使用 uv 运行

### 1. 安装依赖

安装后端依赖：

```bash
cd backend
uv sync
cd ..
```

安装前端依赖：

```bash
cd frontend
corepack pnpm install
cd ..
```

### 2. 初始化数据库

```bash
uv run --project backend python scripts/migrate.py upgrade
```

该命令会创建 SQLite 数据库，并将数据库结构升级到最新 Alembic 版本。应用启动时
不会自动执行迁移，因此首次启动和拉取到新迁移后都需要运行此命令。

### 3. 启动前后端

使用统一脚本同时启动 FastAPI、SQLite Worker 和 Next.js：

```bash
uv run --project backend python scripts/dev.py
```

也可以只启动后端服务组或前端；后端服务组包含 API 和 Worker：

```bash
# 只启动 API 和 Worker
uv run --project backend python scripts/dev.py --backend-only

# 只启动前端
uv run --project backend python scripts/dev.py --frontend-only
```

按 `Ctrl+C` 可以停止由脚本启动的服务。

如果只需要执行一个排队任务，可运行：

```bash
uv run --project backend python scripts/run_worker.py --once
```

## 方式二：不使用 uv 运行

这种方式使用标准 `venv` 和 `pip` 管理后端环境。因为 `scripts/dev.py` 内部会调用
uv，所以此方式需要在三个终端中分别启动 API、Worker 和前端。

### 1. 创建并激活 Python 虚拟环境

Linux 或 macOS：

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
```

Windows PowerShell：

```powershell
py -3.12 -m venv backend/.venv
.\backend\.venv\Scripts\Activate.ps1
```

后续命令中的 `python` 都应指向刚刚激活的虚拟环境。

### 2. 使用 pip 安装后端依赖

运行项目只需要安装主依赖：

```bash
python -m pip install --upgrade pip
python -m pip install -e ./backend
```

如果还需要执行测试、代码检查和类型检查，请额外安装开发依赖：

```bash
python -m pip install mypy pytest pytest-asyncio ruff
```

### 3. 安装前端依赖

```bash
cd frontend
corepack pnpm install
cd ..
```

### 4. 初始化数据库

保持 Python 虚拟环境处于激活状态，然后运行：

```bash
python scripts/migrate.py upgrade
```

### 5. 启动后端

在第一个终端中激活虚拟环境，然后执行：

```bash
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 6. 启动后台 Worker

在第二个终端中激活虚拟环境，然后执行：

```bash
python scripts/run_worker.py
```

Worker 必须保持运行，才能处理资料解析、Embedding 和维护任务。未运行时任务不会丢失，
而是保留在 SQLite 的 `queued` 状态。

### 7. 启动前端

在第三个终端中执行：

```bash
cd frontend
corepack pnpm dev
```

## 服务地址

启动成功后可访问：

- 前端页面：<http://127.0.0.1:3000>
- 后端 OpenAPI 文档：<http://127.0.0.1:8000/docs>
- 后端健康检查：<http://127.0.0.1:8000/api/v1/health>

健康检查的正常响应示例：

```json
{
  "status": "ok",
  "service": "LearnLoop API",
  "version": "0.1.0",
  "environment": "development"
}
```

## 当前学习流程

打开前端首页后，可以完整体验以下学习闭环。计划、讲解和练习由当前配置的
DeepSeek 模型生成；未配置模型时使用确定性模板：

```text
创建学习目标
→ 生成结构化学习计划
→ 在计划页导入个人资料并测试检索
→ SQLite Worker 在后台解析、切块并建立索引
→ 选择计划项并开始学习
→ Agent 检索个人资料，生成带引用讲解并在练习处暂停
→ 提交答案并确认或纠正评分
→ Agent 更新掌握度和下次复习时间
→ 完成学习会话
```

后端 API 的交互式说明和请求结构以 <http://127.0.0.1:8000/docs> 为准。
阶段三对创建目标、开始会话和提交作答使用 `Idempotency-Key` 请求头，前端 API
Client 会自动生成该请求头，避免网络重试造成重复数据。
学习会话页面使用 SSE 展示当前节点和 Tool；连接中断时根据最后事件序号补拉，页面刷新
则通过稳定的 Run/Thread 映射恢复到同一个 Interrupt。

## 数据库迁移命令

使用 uv：

```bash
uv run --project backend python scripts/migrate.py upgrade
uv run --project backend python scripts/migrate.py current
uv run --project backend python scripts/migrate.py downgrade -1
```

不使用 uv 时，先激活 `backend/.venv`，再执行：

```bash
python scripts/migrate.py upgrade
python scripts/migrate.py current
python scripts/migrate.py downgrade -1
```

`downgrade -1` 会回退一个数据库版本，可能删除表或数据，只应在明确需要回滚迁移时
使用。

## 运行检查和测试

### 使用 uv

后端：

```bash
cd backend
uv run pytest
uv run ruff check .
uv run mypy app
cd ..
```

前端：

```bash
cd frontend
corepack pnpm test
corepack pnpm lint
corepack pnpm typecheck
cd ..
```

### 不使用 uv

先激活 `backend/.venv`，然后运行后端检查：

```bash
cd backend
python -m pytest
python -m ruff check .
python -m mypy app
cd ..
```

前端检查与是否使用 uv 无关：

```bash
cd frontend
corepack pnpm test
corepack pnpm lint
corepack pnpm typecheck
cd ..
```

## 常见问题

### 前端提示后端不可用

确认后端正在监听 `127.0.0.1:8000`，并检查 `frontend/.env.local` 中的
`NEXT_PUBLIC_API_BASE_URL`。修改前端环境变量后需要重新启动 Next.js。

### 提示数据库表不存在

应用不会在启动时自动迁移数据库。请先执行对应运行方式下的
`scripts/migrate.py upgrade` 命令。

### Windows 找不到 python 命令

可以使用 `py -3.12` 创建虚拟环境。激活虚拟环境后通常可以直接使用 `python`；如果
PowerShell 禁止执行激活脚本，需要根据本机安全策略允许当前用户执行本地脚本。

### 端口已被占用

默认后端端口为 `8000`，前端端口为 `3000`。停止占用端口的进程后重新运行，或者
分别使用 Uvicorn 和 Next.js 的端口参数启动服务。

## 开发文档

- [文档目录](docs/README.md)
- [分阶段实现流程](docs/implementation-roadmap.md)
- [LangGraph 核心工作流](docs/architecture/langgraph-workflows.md)
- [Checkpoint、Interrupt 与 SSE](docs/architecture/checkpoint-interrupt-sse.md)
- [本地文件与 RAG](docs/architecture/local-rag.md)
- [SQLite 后台任务](docs/architecture/background-jobs.md)
- [自适应学习与复习](docs/architecture/adaptive-review.md)
