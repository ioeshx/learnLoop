# LearnLoop

LearnLoop 是一个本地优先、跨平台的自适应学习 Agent 项目。项目使用
Next.js 提供 Web 界面，FastAPI 提供后端 API，并使用 SQLite 在本地保存学习目标、
知识点、学习计划、练习、掌握度和复习计划。

当前代码覆盖阶段 1–21：v1 学习闭环、v2 动态 Agent，以及 v3 的 Trust/Policy、
Model Gateway、Agent Team 和 Reliability Lab。可以导入 TXT、Markdown、PDF 或单个网页，
基于本地资料学习、研究和复习，并查看 Agent 的执行轨迹、授权决策和评测报告。

默认无需模型密钥即可运行固定学习流程；动态 Agent 需要启用 LLM。阶段编号表示开发路线，
软件包与健康检查中的版本号目前仍为 `0.1.0`。

## 项目目录

```text
learnLoop/
├── backend/
│   ├── app/
│   │   ├── agent/
│   │   │   ├── graphs/、nodes/、states/、tools/  固定 LangGraph 工作流
│   │   │   ├── execution/                     Run、Checkpoint、SSE 与审计存储
│   │   │   ├── dynamic/                       动态内核、Context、Budget、Verifier
│   │   │   ├── memory/、research/、delegation/ Memory、Agentic RAG、Subagent
│   │   │   ├── experience/、optimization/     Reflection、Skill、策略优化
│   │   │   ├── policy/、team/、reliability/   v3 授权、协作与可靠性评测
│   │   │   └── prompts/                      版本化 Prompt
│   │   ├── api/                              REST/SSE 路由与依赖
│   │   ├── application/、domain/              用例服务与领域规则
│   │   ├── infrastructure/                   SQLite、LLM/Gateway、RAG 与文件存储
│   │   └── workers/                          持久化后台任务
│   ├── migrations/                           Alembic 业务数据库迁移
│   ├── evals/                                数据集、Evaluator 与本地报告
│   ├── tests/                                后端测试
│   └── pyproject.toml、uv.lock                Python 依赖与锁文件
├── frontend/
│   ├── src/app/                              学习页面与 Agent 控制台
│   ├── src/lib/api.ts                         API 类型与 SSE Client
│   ├── public/                               PWA Service Worker 与图标
│   ├── tests/                                Vitest 测试
│   └── package.json、pnpm-lock.yaml           前端依赖与锁文件
├── data/                                     本地 SQLite、原始资料等运行数据
├── docs/
│   ├── agent/                                v1–v3 路线、阶段实现与审查文档
│   └── architecture/                         基础架构与运行机制
├── scripts/
│   ├── dev.py                                同时启动 API、Worker、前端
│   ├── migrate.py                            数据库迁移
│   ├── run_worker.py                         独立 Worker
│   └── run_evals.py                           冻结数据集评测入口
└── .env.example                              后端配置示例
```

## 运行环境

两种运行方式都需要：

- Python 3.12 或 3.13。
- Node.js 22 或更新版本。
- Corepack 和 pnpm（`frontend/package.json` 固定为 `pnpm@11.7.0`）。

如果选择 uv 方式，还需要安装 [uv](https://docs.astral.sh/uv/)。如果不使用 uv，
则使用 Python 自带的 `venv` 和 `pip`。

以下命令默认在项目根目录 `learnLoop/` 中执行。
统一启动脚本要求 `uv` 和 `corepack` 在 PATH 中；不使用 uv 时按下文分别启动三个进程。

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

### Agent 与 v3 配置

完整配置项见 [.env.example](.env.example)，类型、范围及默认值以
[Settings](backend/app/config.py) 为准。常用开关如下：

| 配置 | 默认值 | 用途 |
| --- | --- | --- |
| `LEARNLOOP_AGENT_DYNAMIC_WRITES_ENABLED` | `false` | 动态 Agent 默认 Shadow；开启后才允许其写操作 |
| `LEARNLOOP_AGENT_MAX_STEPS` / `LEARNLOOP_AGENT_DEADLINE_SECONDS` | `24` / `300` | 单次动态运行的步数与时间上限 |
| `LEARNLOOP_AGENT_MAX_TOTAL_TOKENS` | `80000` | 动态运行 Token 预算 |
| `LEARNLOOP_AGENT_CONTEXT_TOKENS` | `12000` | Context 编译预算 |
| `LEARNLOOP_AGENT_CONTEXT_DEBUG_FULL` | `false` | 是否在本地保存完整编译 Context |
| `LEARNLOOP_AGENT_MEMORY_ENABLED` | `true` | Memory recall |
| `LEARNLOOP_AGENT_SKILL_LIBRARY_ENABLED` | `true` | 经审核的 Skill recall |
| `LEARNLOOP_AGENT_POLICY_OPTIMIZATION_ENABLED` | `false` | 策略优化运行时开关 |
| `LEARNLOOP_LLM_GATEWAY_ENABLED` | `true` | 已配置模型的 capability routing、预算检查与 circuit breaker |
| `LEARNLOOP_AGENT_TEAM_ENABLED` | `true` | Team runtime；实际创建还依赖模型与 Delegation 服务 |
| `LEARNLOOP_AGENT_TEAM_MAX_PARALLEL_CHILDREN` | `2` | Team 并发上限 |
| `LEARNLOOP_AGENT_RELIABILITY_ENABLED` | `true` | 离线 Reliability Runner |
| `LEARNLOOP_AGENT_RELIABILITY_MAX_TRIALS_PER_SCENARIO` | `20` | API 单场景 Trial 上限 |

Skill、Policy optimization、Trust Policy、Team 和 Reliability 的管理接口分别由
`AGENT_SKILL_ADMIN_ENABLED`、`AGENT_POLICY_ADMIN_ENABLED`、`AGENT_TRUST_POLICY_ADMIN_ENABLED`、
`AGENT_TEAM_ADMIN_ENABLED`、`AGENT_RELIABILITY_ADMIN_ENABLED` 控制；环境变量均需加
`LEARNLOOP_` 前缀，示例配置默认开启。这些是功能开关，不是用户认证机制。

Gateway 当前默认装配单个 DeepSeek Provider；多 Provider fallback 是框架能力，需要在代码中注册
额外 Provider/Profile。成本单价默认是 `0`，需要填写实际部署单价后，estimated-cost 指标才有意义。
示例模型名称来自仓库配置，使用时应按服务账户可用模型调整。远程 LLM 会接收编译到请求中的学习内容；
本地 Embedding 并不意味着启用远程 LLM 后所有推理也在本机完成。

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

Dashboard 位于 <http://127.0.0.1:3000/dashboard>，可查看知识图、掌握度趋势、七日摘要、
最近 Agent 运行并导出本地学习数据。支持 PWA 的浏览器会显示“安装 LearnLoop”入口。

日常学习页面默认使用 `fixed_v1`。启用 LLM 后，可通过
`POST /api/v1/agent/study-sessions/{session_id}/runs?engine_version=dynamic_v2`
启动动态运行，再通过 Run Trace 查看 Plan、Action、Observation、Context 和预算。
`dynamic_v2` 默认 Shadow；需要执行写操作时，再设置 `LEARNLOOP_AGENT_DYNAMIC_WRITES_ENABLED=true`
并重启 API。写操作仍受 Policy 与 Verifier 约束。

研究流程从 `/research` 进入：选择学习目标并提出问题，Research Tutor 在已导入资料范围内检索、
补充查询、综合证据，返回带引用的结论或证据不足说明。到期复习在 `/reviews` 中进行。

## 当前能力

### 学习闭环与本地运行（阶段 1–10）

- FastAPI、Next.js、React、TypeScript、Tailwind CSS；SQLite 异步事务、Alembic 与 Unit of Work。
- 学习目标、知识图、计划、讲解、练习、确定性判分、掌握度事件重算、前置知识补救和 FSRS 复习。
- 固定课程回退与可选 LLM 结构化生成，包含输出校验、修复、重试、超时和 Token 统计。
- LangGraph 工作流、SQLite Checkpoint、人工 Interrupt、持久化事件与 SSE 断线重放。
- 本地资料解析、内容寻址与去重、FTS5 BM25 / 向量混合检索、可核验页码或章节引用。
- 独立 SQLite Worker、租约心跳、崩溃恢复、幂等、取消和退避重试。
- Dashboard、Agent Trace、PWA 应用壳缓存与学习数据 JSON 导出；完整学习功能仍需要本地 API。

### 动态 Agent（阶段 11–17）

- Dynamic Kernel：Plan / Action / Observation / Verify 循环、有界重规划、取消与多维预算。
- Context Engine：分区编译、Token 分配、来源有效期、快照与上下文裁剪。
- Agent Memory：候选提取、审核、纠正、停用、删除与 scope-aware recall。
- Agentic RAG / Research Tutor：有界多轮检索、查询改写、证据综合与 Citation verification。
- Subagent-as-Tool：Researcher 委派、独立 Child Run、scope 隔离与父子预算/取消传播。
- Reflection / Skill Library：有来源的反思、候选 Skill、人工发布、召回和退化隔离。
- Policy Optimization / Agentic RL：Reward hard gate、延迟回报、轨迹审核、Bandit、holdout 与回滚。
  默认关闭在线策略优化；当前交付不等于已完成真实模型 RL 训练。

### 可信协作与可靠性（阶段 18–21）

- Trust / Policy Engine：统一 PDP/PEP、Capability Grant、DataLabel、taint propagation 与审计。
- Model Gateway：能力筛选、cost/deadline preflight、retryable fallback、repair affinity 与 circuit breaker。
- Agent Team：Researcher/Evaluator Role Registry、Task DAG、有界并发、Artifact hash/provenance 和 verified fan-in。
- Reliability Lab：seeded Trial、六类 Fault hook、Safety hard gate、`pass@k` / `pass^k`、Recovery 和 worst-slice。
  默认 Executor 是离线 Contract fixture，未接入真实 Agent 的端到端故障实验。
- 版本化冻结评测集、后端/前端契约测试，以及 Policy、Gateway、Team、Reliability 内部控制台。

Team 当前是本地同进程协作；remote A2A、外部 MCP、进程级 sandbox 和真实在线训练尚未交付。

## 页面与 API 入口

页面以 `http://127.0.0.1:3000` 为基址；接口以 `http://127.0.0.1:8000/api/v1` 为基址。

| 页面 | 用途 | 主要 API |
| --- | --- | --- |
| `/`、`/plans/{id}` | 目标与学习计划 | `/goals`、`/plans` |
| `/study-sessions/{id}`、`/results/{id}` | 学习和作答结果 | `/study-sessions` |
| `/resources` | 本地资料导入与检索 | `/resources` |
| `/reviews`、`/dashboard` | 复习、学习趋势、导出 | `/reviews`、`/data` |
| `/agent-runs/{runId}` | Run Trace、Tool、Model、Context 与 Child Run | `/agent/runs` |
| `/research` | Research Tutor | `/research/runs` |
| `/memories` | Memory 审核与维护 | `/memories` |
| `/agent-skills` | Skill 审核与状态管理 | `/agent/skills` |
| `/agent-optimization` | Policy Lab | `/agent/optimization` |
| `/agent-policy` | 授权决策与审计 | `/agent/policy` |
| `/model-gateway` | Profile、Provider health 与 route | `/agent/model-gateway` |
| `/agent-team` | Role、Task 和 Artifact | `/agent/team` |
| `/agent-reliability` | 离线 suite、指标与 Trial manifest | `/agent/reliability` |

完整路由、参数及响应以运行中的 `/docs` 为准。未配置 LLM 时，部分需要模型的服务不会创建，
对应接口会返回不可用；这不影响固定学习流程。

## 数据与备份

默认数据根目录为 `data/`，相对 `LEARNLOOP_DATA_DIR` 始终按项目根目录解析：

- `data/db/learnloop.db`：学习实体、资料索引、Memory、Research 与后台任务等业务数据。
- `data/db/checkpoints.db`：Checkpoint、Run、事件及 Policy/Team/Reliability 等执行数据。
- `data/files/`：导入的原始资料。
- `backend/evals/reports/`：本地生成的评测报告，不纳入 Git。

Dashboard 的 JSON 导出是学习数据导出，不替代完整数据库与文件备份。完整备份时先停止 API 和
Worker，再复制整个数据目录；`.env` 中的密钥需单独保管。Checkpoint retention 默认 30 天，
不要将该设置理解为所有审计表与 Reliability Report 都已有统一清理机制。

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

默认命令只运行 `v1.json` 基线，不会自动遍历全部阶段数据集：

```bash
# 使用 uv
uv run --project backend python scripts/run_evals.py

# 不使用 uv，先激活 backend/.venv
python scripts/run_evals.py
```

评测报告生成在 `backend/evals/reports/latest.json`，任一指标低于阈值时命令返回非零状态。

指定阶段数据集与报告路径，例如 Reliability：

```bash
uv run --project backend python scripts/run_evals.py --dataset backend/evals/datasets/reliability_v1.json --output backend/evals/reports/reliability-latest.json
```

其他专项数据集为 `memory_v1.json`、`research_v1.json`、`delegation_v1.json`、`skills_v1.json`、
`optimization_v1.json`、`policy_v1.json`、`model_gateway_v1.json`、`agent_team_v1.json`。
完整说明见 [Agent Evaluations](backend/evals/README.md)。冻结集用于回归门禁，不能直接代表生产成功率。

### 使用 uv

后端：

```bash
cd backend
uv run pytest
uv run ruff check app evals tests ../scripts/run_evals.py
uv run mypy app evals ../scripts/run_evals.py
cd ..
```

前端：

```bash
cd frontend
corepack pnpm test
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
cd ..
```

### 不使用 uv

先激活 `backend/.venv`，然后运行后端检查：

```bash
cd backend
python -m pytest
python -m ruff check app evals tests ../scripts/run_evals.py
python -m mypy app evals ../scripts/run_evals.py
cd ..
```

前端检查与是否使用 uv 无关：

```bash
cd frontend
corepack pnpm test
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
cd ..
```

类型检查和 Ruff 命令请在 `backend/` 执行，以使用该目录的 `pyproject.toml` 配置。
历史测试目录不在正式 Mypy 检查范围；Pytest 负责执行全部后端测试，包含冻结评测门禁。

## 本地构建运行

前端开发使用 `pnpm dev`；验证构建产物时使用：

```bash
cd frontend
corepack pnpm build
corepack pnpm start
```

后端可在 `backend/` 下以 `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
运行，去掉开发时的 `--reload`。Worker 仍需独立启动。当前仓库未提供容器或公网部署编排。

## 常见问题

### 导入的资料一直排队

确认独立 Worker 正在运行，并与 API 使用相同 `.env` 和数据目录。可执行
`python scripts/run_worker.py --once` 处理至多一个可领取任务；它不会持续消费队列。

### 动态 Agent 或 Team 返回不可用

`dynamic_v2` 需要启用 LLM；Team 还需启用 `LEARNLOOP_AGENT_TEAM_ENABLED`。
修改配置后重启 API。Shadow 模式下写操作受限，应检查 Trace 中的 Policy/Verifier 决策。

### Reliability 分数很高，是否表示真实模型已达标

默认运行的是 Contract fixture，验证 Fault schedule、Grader、聚合与报告链路。
真实模型可靠性需要接入实际 Executor 并运行独立实验，参见阶段 21 文档。

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
如果更改前端 origin，还需相应调整后端 `LEARNLOOP_CORS_ORIGINS`，例如
`["http://127.0.0.1:3001"]`；更改 API 地址时同步修改 `NEXT_PUBLIC_API_BASE_URL`。

## 开发文档

- [文档目录](docs/README.md)
- [v1 阶段 1–10 路线](docs/agent/v1-implementation-roadmap.md)
- [v2 阶段 11–17 路线](docs/agent/v2-roadmap.md)
- [v3 阶段 18–21 路线](docs/agent/v3-agent-roadmap.md)
- [v3 执行与完成审计](docs/agent/v3-execution-plan.md)
- [LangGraph 核心工作流](docs/architecture/langgraph-workflows.md)
- [Checkpoint、Interrupt 与 SSE](docs/architecture/checkpoint-interrupt-sse.md)
- [本地文件与 RAG](docs/architecture/local-rag.md)
- [SQLite 后台任务](docs/architecture/background-jobs.md)
- [自适应学习与复习](docs/architecture/adaptive-review.md)
- [评测、可观测性与交付完善](docs/architecture/evaluation-observability.md)
- [Dynamic Agent Kernel](docs/agent/v2-11-dynamic-agent-kernel.md)
- [Context Engine](docs/agent/v2-12-context-engine.md)
- [Agent Memory](docs/agent/v2-13-agent-memory.md)
- [Agentic RAG / Research Tutor](docs/agent/v2-14-agentic-rag-research-tutor.md)
- [Subagent-as-Tool](docs/agent/v2-15-subagent-as-tool.md)
- [Reflection / Skill Library](docs/agent/v2-16-reflection-skill-library.md)
- [策略优化与 Agentic RL](docs/agent/v2-17-policy-optimization-agentic-rl.md)
- [Trust / Policy Engine](docs/agent/v3-18-trust-policy-engine.md)
- [Model Gateway](docs/agent/v3-19-model-gateway.md)
- [Agent Team](docs/agent/v3-20-agent-team.md)
- [Reliability Lab](docs/agent/v3-21-agent-reliability.md)
