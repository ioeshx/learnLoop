# Runtime Data

该目录保存 LearnLoop 的本地运行时数据：

- `db/learnloop.db`：业务数据和后台任务。
- `db/checkpoints.db`：LangGraph Checkpoint。
- `resources/original/`：用户导入的原始资料。
- `resources/derived/`：解析文本、缩略图等派生产物。
- `exports/`：用户主动导出的数据。
- `logs/`：本地日志。
- `temp/`：可清理的临时文件。

除占位文件外，以上内容均被 Git 忽略。

