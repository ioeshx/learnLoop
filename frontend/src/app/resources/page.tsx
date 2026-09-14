"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useEffect, useState } from "react";

import {
  cancelJob,
  deleteResource,
  fetchJob,
  fetchJobs,
  fetchResources,
  fetchStudyPlan,
  importResourceUrl,
  retryJob,
  searchResources,
  uploadResource,
  type BackgroundJob,
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
  const [jobs, setJobs] = useState<Record<string, BackgroundJob>>({});
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
    void refreshWorkspace(goalId, setResources, setJobs, setError);
  }, [goalId]);

  const activeJobIds = Object.values(jobs)
    .filter((job) => job.status === "queued" || job.status === "running")
    .map((job) => job.id)
    .sort()
    .join(",");

  useEffect(() => {
    if (!activeJobIds) return;
    let disposed = false;
    async function poll() {
      try {
        const latest = await Promise.all(
          activeJobIds.split(",").map((jobId) => fetchJob(jobId)),
        );
        if (disposed) return;
        setJobs((current) => mergeResourceJobs(current, latest));
        if (latest.some((job) => isTerminal(job.status)) && goalId) {
          setResources(await fetchResources(goalId));
        }
      } catch (caught) {
        if (!disposed) {
          setError(caught instanceof Error ? caught.message : "读取任务状态失败");
        }
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 1_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [activeJobIds, goalId]);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    if (!goalId) return;
    const data = new FormData(formElement);
    const file = data.get("file");
    if (!(file instanceof File) || !file.size) return;
    await run(async () => {
      const submission = await uploadResource(file, goalId, nodeId || undefined);
      setJobs((current) => mergeResourceJobs(current, [submission.job]));
      await refreshWorkspace(goalId, setResources, setJobs, setError);
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
      const submission = await importResourceUrl(url, goalId, nodeId || undefined);
      setJobs((current) => mergeResourceJobs(current, [submission.job]));
      await refreshWorkspace(goalId, setResources, setJobs, setError);
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
      await refreshWorkspace(goalId, setResources, setJobs, setError);
      setCitations((items) =>
        items.filter((item) => item.resource_id !== resourceId),
      );
    });
  }

  async function cancel(jobId: string) {
    await run(async () => {
      const job = await cancelJob(jobId);
      setJobs((current) => mergeResourceJobs(current, [job]));
    });
  }

  async function retry(jobId: string) {
    await run(async () => {
      const job = await retryJob(jobId);
      setJobs((current) => mergeResourceJobs(current, [job]));
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
        <Link href="/research">打开 Research Tutor →</Link>
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
            {working ? "正在提交…" : "上传并加入后台队列"}
          </button>
        </form>

        <form className="form-card" onSubmit={importUrl}>
          <div>
            <p className="step-label">网页</p>
            <h2>导入单个页面</h2>
          </div>
          <input name="url" placeholder="https://example.com/lesson" required type="url" />
          <button className="primary-button" disabled={!goalId || working}>
            {working ? "正在抓取…" : "抓取并加入后台队列"}
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
          resources.map((resource) => {
            const job = jobs[resource.id];
            return (
              <article className="resource-row" key={resource.id}>
                <div>
                  <strong>{resource.title}</strong>
                  <p>
                    {resource.source_type === "file" ? "本地文件" : "网页"} ·{" "}
                    {(resource.size_bytes / 1024).toFixed(1)} KB ·{" "}
                    {resourceStatus(resource.status)}
                  </p>
                  {job ? (
                    <div className="resource-job">
                      <progress max="100" value={job.progress} />
                      <span>
                        {job.progress_message ?? job.status} · 第 {job.attempts}/
                        {job.max_attempts} 次尝试
                      </span>
                      {job.last_error ? <small>{job.last_error}</small> : null}
                    </div>
                  ) : null}
                </div>
                <div className="resource-actions">
                  {job && (job.status === "queued" || job.status === "running") ? (
                    <button
                      className="secondary-button"
                      disabled={working}
                      onClick={() => void cancel(job.id)}
                      type="button"
                    >
                      取消任务
                    </button>
                  ) : null}
                  {job && (job.status === "failed" || job.status === "cancelled") ? (
                    <button
                      className="secondary-button"
                      disabled={working}
                      onClick={() => void retry(job.id)}
                      type="button"
                    >
                      重试
                    </button>
                  ) : null}
                  <button
                    className="secondary-button"
                    disabled={working}
                    onClick={() => void remove(resource.id)}
                    type="button"
                  >
                    删除
                  </button>
                </div>
              </article>
            );
          })
        ) : (
          <p className="loading-card">这个目标还没有个人资料。</p>
        )}
      </section>
    </main>
  );
}

async function refreshWorkspace(
  goalId: string,
  update: (resources: LearningResource[]) => void,
  updateJobs: (jobs: Record<string, BackgroundJob>) => void,
  fail: (message: string) => void,
) {
  try {
    const [resources, recentJobs] = await Promise.all([
      fetchResources(goalId),
      fetchJobs("resource.process"),
    ]);
    update(resources);
    const resourceIds = new Set(resources.map((resource) => resource.id));
    updateJobs(
      mergeResourceJobs(
        {},
        recentJobs.filter((job) =>
          resourceIds.has(String(job.payload.resource_id ?? "")),
        ),
      ),
    );
  } catch (caught) {
    fail(caught instanceof Error ? caught.message : "读取资料失败");
  }
}

function mergeResourceJobs(
  current: Record<string, BackgroundJob>,
  jobs: BackgroundJob[],
): Record<string, BackgroundJob> {
  const merged = { ...current };
  for (const job of jobs) {
    const resourceId = job.payload.resource_id;
    if (typeof resourceId === "string") merged[resourceId] = job;
  }
  return merged;
}

function isTerminal(status: BackgroundJob["status"]): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}

function resourceStatus(status: LearningResource["status"]): string {
  if (status === "ready") return "索引就绪";
  if (status === "failed") return "处理失败";
  return "后台处理中";
}
