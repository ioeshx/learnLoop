"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useState } from "react";

import {
  deleteResource,
  fetchResources,
  fetchStudyPlan,
  importResourceUrl,
  searchResources,
  uploadResource,
  type LearningResource,
  type PlanItem,
  type ResourceCitation,
} from "@/lib/api";

export default function ResourcesPage() {
  return (
    <Suspense fallback={<p className="loading-card">正在读取资料库…</p>}>
      <ResourceWorkspace />
    </Suspense>
  );
}

function ResourceWorkspace() {
  const parameters = useSearchParams();
  const [goalId, setGoalId] = useState(parameters.get("goalId") ?? "");
  const [planItems, setPlanItems] = useState<PlanItem[]>([]);
  const [nodeId, setNodeId] = useState("");
  const [resources, setResources] = useState<LearningResource[]>([]);
  const [citations, setCitations] = useState<ResourceCitation[]>([]);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const planId = parameters.get("planId");
    if (!planId) return;
    fetchStudyPlan(planId)
      .then((plan) => {
        setGoalId(plan.goal_id);
        setPlanItems(plan.items);
      })
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : "读取计划失败"),
      );
  }, [parameters]);

  useEffect(() => {
    if (!goalId) return;
    void refreshResources(goalId, setResources, setError);
  }, [goalId]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    if (!goalId) return;
    const data = new FormData(formElement);
    const file = data.get("file");
    if (!(file instanceof File) || !file.size) return;
    await run(async () => {
      await uploadResource(file, goalId, nodeId || undefined);
      await refreshResources(goalId, setResources, setError);
      formElement.reset();
    });
  }

  async function importUrl(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    if (!goalId) return;
    const data = new FormData(formElement);
    const url = String(data.get("url") ?? "");
    await run(async () => {
      await importResourceUrl(url, goalId, nodeId || undefined);
      await refreshResources(goalId, setResources, setError);
      formElement.reset();
    });
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!goalId) return;
    const data = new FormData(event.currentTarget);
    const query = String(data.get("query") ?? "");
    await run(async () => {
      setCitations(
        await searchResources(query, goalId, nodeId || undefined),
      );
    });
  }

  async function remove(resourceId: string) {
    await run(async () => {
      await deleteResource(resourceId);
      await refreshResources(goalId, setResources, setError);
      setCitations((items) =>
        items.filter((item) => item.resource_id !== resourceId),
      );
    });
  }

  async function run(operation: () => Promise<void>) {
    setWorking(true);
    setError(null);
    try {
      await operation();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "资料操作失败");
    } finally {
      setWorking(false);
    }
  }

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/">← 返回首页</Link>
        <span>个人资料库</span>
      </nav>

      <p className="eyebrow">LOCAL-FIRST · HYBRID RAG</p>
      <h1 className="page-title">让 Agent 从你的资料中学习。</h1>
      <p className="page-description">
        支持 TXT、Markdown、PDF 和单个网页。文件保存在本地，检索融合 FTS5
        关键词排名与向量相似度，并保留页码或章节引用。
      </p>

      {!parameters.get("goalId") ? (
        <label className="resource-goal-field">
          学习目标 ID
          <input
            onChange={(event) => setGoalId(event.target.value.trim())}
            placeholder="粘贴目标 ID"
            value={goalId}
          />
        </label>
      ) : null}

      {planItems.length ? (
        <label className="resource-goal-field">
          关联知识点（留空则关联整个目标）
          <select onChange={(event) => setNodeId(event.target.value)} value={nodeId}>
            <option value="">整个学习目标</option>
            {planItems.map((item) => (
              <option key={item.id} value={item.knowledge_node_id}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {error ? <p className="error-banner">{error}</p> : null}

      <section className="resource-form-grid">
        <form className="form-card" onSubmit={upload}>
          <div>
            <p className="step-label">本地文件</p>
            <h2>导入学习资料</h2>
          </div>
          <input accept=".txt,.md,.markdown,.pdf" name="file" required type="file" />
          <button className="primary-button" disabled={!goalId || working}>
            {working ? "正在解析与索引…" : "上传并建立索引"}
          </button>
        </form>

        <form className="form-card" onSubmit={importUrl}>
          <div>
            <p className="step-label">网页</p>
            <h2>导入单个页面</h2>
          </div>
          <input name="url" placeholder="https://example.com/lesson" required type="url" />
          <button className="primary-button" disabled={!goalId || working}>
            {working ? "正在抓取与索引…" : "导入网页"}
          </button>
        </form>
      </section>

      <form className="resource-search" onSubmit={search}>
        <input name="query" placeholder="例如：BFS 为什么使用队列？" required />
        <button className="secondary-button" disabled={!goalId || working}>
          测试混合检索
        </button>
      </form>

      {citations.length ? (
        <section className="citation-list">
          <h2>检索引用</h2>
          {citations.map((citation) => (
            <article className="citation-card" key={citation.chunk_id}>
              <strong>{citation.title}</strong>
              <span>{citation.locator ?? "全文"}</span>
              <p>{citation.excerpt}</p>
            </article>
          ))}
        </section>
      ) : null}

      <section className="resource-list">
        <h2>已导入资料</h2>
        {resources.length ? (
          resources.map((resource) => (
            <article className="resource-row" key={resource.id}>
              <div>
                <strong>{resource.title}</strong>
                <p>
                  {resource.source_type === "file" ? "本地文件" : "网页"} ·{" "}
                  {(resource.size_bytes / 1024).toFixed(1)} KB · {resource.status}
                </p>
              </div>
              <button
                className="secondary-button"
                disabled={working}
                onClick={() => void remove(resource.id)}
                type="button"
              >
                删除
              </button>
            </article>
          ))
        ) : (
          <p className="loading-card">这个目标还没有个人资料。</p>
        )}
      </section>
    </main>
  );
}

async function refreshResources(
  goalId: string,
  update: (resources: LearningResource[]) => void,
  fail: (message: string) => void,
) {
  try {
    update(await fetchResources(goalId));
  } catch (caught) {
    fail(caught instanceof Error ? caught.message : "读取资料失败");
  }
}
