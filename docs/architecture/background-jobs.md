# SQLite 后台任务

## 目标与边界

阶段八在不引入 Redis、PostgreSQL、Celery 或 Docker 的前提下，实现一个可以跨平台运行的
持久化任务系统。FastAPI 负责接收请求和入队，独立 Python Worker 负责耗时工作：

```text
Browser → FastAPI → background_jobs (SQLite)
                         ↓ claim + lease
                    single Worker
                         ↓ progress / result
                    background_jobs
```

第一版只运行一个 Worker 进程，但领取协议本身是原子的；未来扩展多个 Worker 时不需要改变
表结构和 Handler 接口。

## 数据模型

`background_jobs` 保存：

- `job_type`、`payload_json` 和 `result_json`。
- `queued / running / succeeded / failed / cancelled` 状态。
- 0–100 的进度、进度说明、尝试次数和最后错误。
- `available_at`、租约所有者和租约到期时间。
- 可选幂等键、取消标记以及创建/开始/结束时间。

同一 `job_type + idempotency_key` 只能创建一条记录。重复请求载荷相同时返回原 Job，载荷
不同时返回冲突，避免网络重试重复执行周报或复习任务。

## 领取、租约与恢复

Worker 使用 `BEGIN IMMEDIATE` 完成以下原子过程：

1. 将已过期且仍可重试的租约恢复为 `queued`。
2. 将已耗尽尝试次数的过期任务标记为 `failed`。
3. 领取最早到期的 `queued` Job，并把尝试次数加一。
4. 写入 Worker ID 和租约到期时间后提交事务。

运行期间，后台心跳每隔约三分之一租期续约；Handler 上报进度时也会续约。Worker 崩溃后
不需要人工清锁，另一次轮询会在租约到期后恢复任务。

## 重试、取消与进度

- 普通运行异常按照 `base × 2^(attempt-1)` 指数退避，并受最大延迟限制。
- 无效文件、缺失资料和非法 Job 载荷属于永久失败，不进行无意义重试。
- 排队任务可以立即取消；运行任务设置 `cancel_requested`，Handler 在阶段边界协作退出。
- 用户可以将 `failed` 或 `cancelled` Job 手动重置为 `queued`。
- 每次进度写入都校验租约所有者，失去租约的进程不能覆盖新 Worker 的状态。

## 第一批 Handler

| Job Type | 工作 | 持久化结果 |
| --- | --- | --- |
| `resource.process` | 解析原始文件、切块、生成 Embedding、原子替换索引 | Resource ID 和索引时间 |
| `report.weekly` | 聚合会话、练习、正确率、掌握度和待复习数 | 周期统计 JSON |
| `reviews.generate_due` | 按截止时间生成到期复习快照 | 可执行复习项 JSON |

资料上传仍需在 HTTP 请求中把原始字节安全落盘；CPU/网络开销更大的解析和 Embedding 已移到
Worker。周报和复习快照暂存在 Job 的 `result_json`，阶段十的 Dashboard 可以直接读取或再
迁移到独立投影表。

## API

```text
GET  /api/v1/jobs
GET  /api/v1/jobs/{job_id}
POST /api/v1/jobs/{job_id}/cancel
POST /api/v1/jobs/{job_id}/retry
POST /api/v1/jobs/weekly-reports
POST /api/v1/jobs/due-reviews
```

文件和 URL 导入接口现在返回 HTTP `202`，响应同时包含 `resource` 和 `job`。前端每秒查询
活动任务，展示进度、尝试次数、错误、取消与重试操作。

## 运行

统一开发脚本默认同时启动 API、Worker 和前端：

```bash
uv run --project backend python scripts/dev.py
```

单独运行 Worker：

```bash
uv run --project backend python scripts/run_worker.py
uv run --project backend python scripts/run_worker.py --once
```

`--once` 最多领取一个任务后退出，适合维护脚本和测试。应用不会在 FastAPI 进程中执行
任务，因此只启动 Uvicorn 时，任务会安全保留在 `queued`，直到 Worker 启动。

## 第一版限制

- 只建议启动一个 Worker，不提供水平扩容配置或队列优先级。
- 不包含定时调度器；周报和到期复习任务由 API 或未来调度器显式入队。
- 取消是协作式的，无法中断一次已经发出的第三方 Embedding HTTP 请求。
- SQLite 适合本地个人应用；高吞吐分布式部署属于未来演进范围。

