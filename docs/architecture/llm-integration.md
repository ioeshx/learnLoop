# LLM 接入架构

## 目标与边界

阶段四只负责把模型变成可靠、可替换的结构化内容生成能力，不引入 LangGraph。
此时领域模型、客观题判分、掌握度和 FSRS 仍是确定性代码。阶段五会在此模型边界之上
编排 Agent 工作流。

```text
Versioned Prompt + Pydantic JSON Schema
                    ↓
              StructuredModel
                    ↓
       ModelProvider Protocol
          ↙                 ↘
 FakeModelProvider    DeepSeekModelProvider
                    ↓
 Pydantic 输出校验（失败时最多修复一次）
                    ↓
 知识图、计划顺序、领域实体校验
                    ↓
             SQLite 事务写入
```

模型调用在数据库事务之外执行，避免等待远程响应时长期占用 SQLite 连接或写事务。
写入前会再次检查目标和已有计划，保证网络重试不会无条件覆盖现有数据。

## 结构化输出

`backend/app/agent/schemas.py` 定义八个严格的 Pydantic 契约：

- `GoalClarification`
- `KnowledgeGraphProposal`
- `StudyPlanProposal`
- `LessonContent`
- `ExerciseProposal`
- `AnswerEvaluation`
- `MisconceptionDiagnosis`
- `StudySummary`

所有契约拒绝未知字段并限制集合长度、文本长度和数值范围。模型响应直接作为 JSON
解析，不从任意 Markdown 中提取数据。如果第一次 JSON 或字段校验失败，
`StructuredModel` 会携带校验错误请求一次修复；第二次仍失败则终止。

## Prompt 管理

当前包含目标澄清、知识图、学习计划、讲解、练习和简答评价六个 Prompt。每个
Prompt 都声明唯一名称、语义版本、使用场景、输入 Schema、输出 Schema 和测试输入。
Prompt 的 JSON Schema 由 Pydantic 在运行时生成并加入 system message。

## Provider

`ModelProvider` 只暴露结构化生成需要的最小能力。`FakeModelProvider` 按 Prompt 名称
返回排队结果，默认用于测试；`DeepSeekModelProvider` 调用 OpenAI 兼容的
`POST /chat/completions`，启用 JSON Output，并读取响应中的实际 Token usage。

DeepSeek Provider 对网络错误、超时、HTTP 429 和常见 5xx 状态进行有限指数退避，
认证和参数错误不重试。默认最多重试两次，每次成功请求的输入、输出及总 Token 会
累计到进程内统计器。外部 API 的当前字段定义以
[DeepSeek Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)
为准。

## 运行配置

默认模式不需要密钥：

```dotenv
LEARNLOOP_LLM_PROVIDER=none
```

启用 DeepSeek：

```dotenv
LEARNLOOP_LLM_PROVIDER=deepseek
LEARNLOOP_LLM_MODEL=deepseek-v4-flash
LEARNLOOP_LLM_API_KEY=替换为你的密钥
LEARNLOOP_LLM_BASE_URL=https://api.deepseek.com
LEARNLOOP_LLM_TIMEOUT_SECONDS=60
LEARNLOOP_LLM_MAX_RETRIES=2
```

密钥使用 Pydantic `SecretStr` 保存，不会出现在配置对象的日志或 `repr` 中。测试和
CI 不读取真实密钥，也不访问外部模型服务。

## 持久化前领域校验

LLM 课程生成器执行以下确定性检查：

1. 知识点 key、边引用和选择题字段先通过 Pydantic。
2. `validate_knowledge_graph` 检查跨目标引用、重复边和 prerequisite 环。
3. 学习计划必须恰好覆盖全部知识点。
4. prerequisite 的源节点必须排在目标节点之前。
5. 知识点、计划和练习必须由领域构造器成功创建。

任何一步失败都会返回稳定的 `curriculum_generation_failed` 应用错误，并且不会写入
部分知识点、计划或练习。
