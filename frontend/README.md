# Frontend

LearnLoop 的 Next.js、React 和 TypeScript Web 客户端。当前实现无 LLM 学习闭环的
四个页面：创建目标、查看计划、完成学习和查看作答结果。

```text
src/app/         路由、布局和页面
src/components/  跨功能共享 UI 组件
src/features/    按业务能力组织的前端模块
src/lib/         API 客户端、SSE、配置和通用工具
tests/           前端集成与端到端测试
```

## 本地运行

在仓库根目录创建 `.env`，并在当前目录创建 `.env.local`。可从对应的示例文件复制。
后端默认运行在 `http://127.0.0.1:8000`。

```bash
corepack pnpm install
corepack pnpm dev
```

前端地址为 <http://127.0.0.1:3000>。完整的前后端安装、数据库迁移和 uv / venv
运行方式见仓库根目录的 `README.md`。

## 质量检查

```bash
corepack pnpm test
corepack pnpm lint
corepack pnpm typecheck
corepack pnpm build
```
