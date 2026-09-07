# 本地文件与 RAG

## 范围

阶段七实现面向个人资料的同步导入和检索闭环：

```text
本地文件 / 单个网页
→ 内容寻址存储
→ TXT / Markdown / PDF / HTML 解析
→ 带页码或章节的分块
→ Embedding
→ SQLite Chunk + FTS5 索引
→ BM25 / 余弦相似度
→ Reciprocal Rank Fusion
→ Agent Tool 与可验证引用
```

阶段八已经在不改变存储、解析和检索接口的前提下，将解析、切块和 Embedding 迁移到
SQLite Worker。HTTP 请求只负责校验范围、获取或保存原始资料并持久化 Job。

## 文件存储

`DocumentStorage` 隔离上层代码与具体文件系统，第一版实现为
`LocalDocumentStorage`，默认目录是 `data/files/`：

```text
data/files/
├── objects/{sha256 前两位}/{sha256 剩余部分}
├── tmp/
└── trash/
```

- 上传时流式计算 SHA-256，并限制最大字节数。
- 先写同文件系统临时文件，`fsync` 后使用原子移动。
- 相同内容只保存一份对象，不同目标或知识点可以共享该对象。
- 删除 Resource 后，最后一个引用对应的对象移动到 `trash/`，而不是直接永久删除。
- 所有 storage key 都必须是根目录内的相对路径，拒绝绝对路径和 `..` 路径穿越。

## 解析与分块

| 类型 | 解析方式 | 引用定位 |
| --- | --- | --- |
| TXT | UTF-8 文本 | 全文 |
| Markdown | 标题感知解析 | 最近标题 |
| PDF | pypdf 文本提取 | 页码 |
| URL | httpx 获取，Beautiful Soup 提取正文 | 页面标题 |

扫描 PDF 没有可提取文本时返回明确错误，第一版不做 OCR。网页导入只允许 HTTP/HTTPS、
80/443 端口和公网 IP；每次跳转都会重新校验，阻止 localhost、私网、链路本地地址以及
带用户名密码的 URL。单次导入只抓取一个页面，不递归爬取链接。

分块默认最大 1000 字符、重叠 150 字符，优先在段落、换行、句号和空格处截断。每个
Chunk 保存 `page_number` 或 `section`，引用不依赖模型推测定位信息。

## 数据模型

业务数据库新增：

- `learning_resources`：来源、业务范围、文件摘要、存储 key、状态和错误。
- `document_chunks`：正文、位置、页码/章节、Embedding 和模型名。
- `document_chunks_fts`：以 `document_chunks` 为 external content 的 FTS5 虚拟表。

三个 SQLite Trigger 负责同步 Chunk 的新增、删除和修改，避免业务表与全文索引漂移。
迁移版本为 `0004_learning_resources_rag`。

## Embedding

`EmbeddingProvider` 提供 `embed_documents` 和 `embed_query` 两个接口：

- `local`：默认离线特征哈希实现，384 维，不发送任何资料到外部服务。
- `openai_compatible`：调用配置地址的 `/embeddings`，校验结果数量、顺序和维度。
- `FakeEmbeddingProvider`：测试中精确控制向量和预期排名。

切换远程 Provider 不需要重写解析和检索层。现有 Chunk 会保留生成时的
`embedding_model`。切换模型后的批量重建入口仍留作后续管理功能。

## 混合检索

检索始终限制在同一个 `goal_id`；指定 `knowledge_node_id` 时，同时检索该知识点专属资料
和目标级公共资料。

1. FTS5 使用 `bm25()` 获取关键词排名。
2. NumPy 计算查询和候选 Chunk 的余弦相似度。
3. 两个排名使用 RRF 合并：`score += 1 / (60 + rank)`。
4. 返回 Top K Chunk，以及真实的 resource、chunk、页码/章节和来源 URL。

向量候选相似度低于 0.15 时不进入结果；词法和向量均无支持时返回空列表。Agent 不会在
空结果上创建引用。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/resources/files` | multipart 导入 TXT、Markdown 或 PDF |
| `POST` | `/api/v1/resources/url` | 导入一个网页 URL |
| `GET` | `/api/v1/resources` | 按目标或知识点列出资料 |
| `GET` | `/api/v1/resources/search` | 测试混合检索和引用 |
| `GET` | `/api/v1/resources/{id}` | 查看资料元数据 |
| `DELETE` | `/api/v1/resources/{id}` | 删除索引并将最后一份文件移入回收区 |

前端从学习计划进入“管理个人资料”，可以选择关联整个目标或某个知识点，完成文件/网页
导入并直接检查召回结果。

## Agent 接入

每日学习图的 `retrieve_sources` 节点调用 `search_learning_resources` Tool。Tool 返回真实
Chunk ID、excerpt、score 和 locator：

- 有资料时，最多五段内容进入 Lesson Prompt，讲解和练习显示确定性的引用附录。
- 无资料时，Prompt 收到空数组，讲解和练习不显示引用。
- 页码和章节来自解析器及数据库，不允许 LLM 自行生成。
