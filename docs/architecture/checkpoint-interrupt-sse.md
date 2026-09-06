# Checkpoint、Interrupt 与 SSE

## 目标与边界

阶段六把阶段五的内存内 Graph 变成可以暂停、重启恢复并由浏览器持续观察的 Agent
执行系统。业务数据仍保存在 `data/db/learnloop.db`；LangGraph Checkpoint、运行映射和
可重放事件保存在独立的 `data/db/checkpoints.db`。

Checkpoint 数据库由 LangGraph 和执行层在应用启动时自行建表，不属于业务数据库的
Alembic 迁移。这样可以独立替换 Checkpointer，也避免把 LangGraph 内部表耦合进领域模型。

## 执行架构

```text
浏览器
  ├─ POST 创建/恢复 Agent Run
  ├─ POST Command(resume=用户输入)
  └─ GET  SSE 重放（Last-Event-ID）
             │
             ▼
FastAPI Agent Run API
             │
             ├─ AgentRuntime ── 编译后的 StateGraph
             │                       │
             │                       └─ AsyncSqliteSaver
             │
             └─ SqliteAgentRunStore
                    ├─ run_id ↔ thread_id ↔ 业务 ID
                    └─ 带 sequence 的标准化事件
```

一次业务流程对应一个稳定的 `run_id` 和 `thread_id`。每日学习图以
`(daily_learning, study_session_id)` 唯一映射，目标规划图以
`(goal_planning, goal_id)` 唯一映射。刷新页面或应用重启不会创建另一条线程。

Graph 在后台 `asyncio.Task` 中继续运行，SSE 客户端断开不会取消 Graph。节点和 Tool
事件先写入 SQLite，再由 SSE 查询并发送，因此客户端可以安全重放，不依赖进程内消息队列。

## Run 生命周期

```text
created → running → awaiting_input ── resume ──→ running
                     ▲                           │
                     └──── 后续 interrupt ───────┘

running → completed
running → failed
```

- `created`：已经建立 run/thread 映射，尚未执行。
- `running`：Graph 正在执行节点。
- `awaiting_input`：Checkpoint 已保存，等待 `Command(resume=...)`。
- `completed`：Graph 正常结束。
- `failed`：执行异常，错误已转换为 `run_failed` 事件。

## Human-in-the-loop 契约

### 练习作答

`answer_required` 包含当前练习，恢复值为：

```json
{"selected_options": ["选项 A"]}
```

### 评分确认与纠正

`grade_review_required` 包含判分结果和原答案。接受评分：

```json
{"action": "accept"}
```

修改答案并要求重新判分：

```json
{"action": "revise_answer", "selected_options": ["选项 B"]}
```

重新判分仍调用确定性领域规则。评分被确认前不会写入 Attempt、Mastery Event 或复习计划。

### 计划审批与编辑

`plan_approval_required` 包含知识图和计划草案。支持：

```json
{"action": "approve"}
{"action": "reject"}
{"action": "edit", "study_plan": {"items": []}}
```

编辑后的计划会再次通过 Schema 和知识依赖顺序校验，校验成功后才可持久化。

### 资料解析歧义

阶段六提供 `confirm_resource_ambiguities` 可复用节点。阶段七接入完整资料处理图时，使用：

```json
{"resolutions": [{"field": "heading_level", "selected": 2}]}
```

## HTTP 与 SSE API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/agent/study-sessions/{id}/runs` | 创建或读取每日学习 Run，并返回 SSE |
| `POST` | `/api/v1/agent/goals/{id}/runs` | 创建或读取目标规划 Run，并返回 SSE |
| `POST` | `/api/v1/agent/runs/{run_id}/resume` | 提交 Interrupt 恢复值，并返回后续 SSE |
| `GET` | `/api/v1/agent/runs/{run_id}` | 查询运行元数据和状态 |
| `GET` | `/api/v1/agent/runs/{run_id}/state` | 读取当前 Checkpoint State |
| `GET` | `/api/v1/agent/runs/{run_id}/events` | 从事件序号重放 SSE |
| `DELETE` | `/api/v1/agent/runs/{run_id}` | 删除 Run、事件和 Checkpoint |

每个 SSE 事件都有单调递增的 `sequence`：

```json
{
  "run_id": "run_123",
  "sequence": 12,
  "event": "node_started",
  "node": "generate_exercise",
  "timestamp": "2026-09-06T12:00:00+00:00",
  "data": {}
}
```

事件类型为 `run_started`、`node_started`、`node_completed`、`tool_started`、
`tool_completed`、`interrupt_created`、`run_completed` 和 `run_failed`。响应头包含
`X-Agent-Run-Id` 和 `X-Agent-Thread-Id`。

## 重连与恢复

前端记录最后收到的 `sequence`。流在尚未到达 Interrupt、完成或失败事件前断开时，前端
最多进行三次指数退避重连：

```http
GET /api/v1/agent/runs/{run_id}/events?after=12
Last-Event-ID: 12
```

服务器只返回序号大于 12 的事件，从而避免 UI 重复处理。正常到达
`interrupt_created` 时 SSE 主动结束；用户输入通过 resume 接口开启下一段流。

跨进程恢复依赖稳定 `thread_id`：应用关闭后重新打开同一个 Run，Checkpointer 从
`checkpoints.db` 读取 Interrupt 所在 State，再用 `Command(resume=...)` 继续。自动化测试
覆盖了“等待作答时关闭 Checkpointer、重新打开、确认评分并完成”的完整路径。

## 清理规则

`LEARNLOOP_CHECKPOINT_RETENTION_DAYS` 默认为 30 天。应用启动时删除超过保留期的
`completed` 和 `failed` Run，同时删除对应事件与 LangGraph Checkpoint。
`awaiting_input` 不会自动清理，避免用户尚未完成的学习或审批流程丢失。用户也可以调用
DELETE 接口显式清除单个非运行中的 Run。
