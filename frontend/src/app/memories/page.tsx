"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  approveMemory,
  correctMemory,
  deactivateMemory,
  deleteMemory,
  fetchMemories,
  rejectMemory,
  type AgentMemory,
} from "@/lib/api";

const FILTERS: Array<{ label: string; value: AgentMemory["status"] | "all" }> = [
  { label: "全部", value: "all" },
  { label: "待审批", value: "candidate" },
  { label: "使用中", value: "active" },
  { label: "已拒绝", value: "rejected" },
  { label: "已遗忘", value: "expired" },
];

export default function MemoriesPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["value"]>("all");
  const [memories, setMemories] = useState<AgentMemory[]>([]);
  const [editing, setEditing] = useState<AgentMemory | null>(null);
  const [content, setContent] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setMemories(await fetchMemories(filter === "all" ? undefined : filter));
      setError(null);
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "读取 Memory 失败");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    let active = true;
    fetchMemories(filter === "all" ? undefined : filter)
      .then((items) => {
        if (active) {
          setMemories(items);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "读取 Memory 失败");
        }
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [filter]);

  async function mutate(operation: () => Promise<unknown>) {
    try {
      setLoading(true);
      await operation();
      await refresh();
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Memory 操作失败");
    }
  }

  return (
    <main className="page-shell memory-shell">
      <nav className="page-nav">
        <Link href="/dashboard">← 返回仪表盘</Link>
      </nav>
      <p className="eyebrow">AGENT MEMORY GOVERNANCE</p>
      <h1 className="page-title">系统记住了什么</h1>
      <p className="page-description">
        查看 Agent 跨 Session 使用的长期 Memory、来源证据、可信度与版本；高影响画像只有审批后才进入 Context。
      </p>

      <div className="memory-filters" aria-label="Memory 状态筛选">
        {FILTERS.map((item) => (
          <button
            className={filter === item.value ? "active" : ""}
            key={item.value}
            onClick={() => {
              setLoading(true);
              setFilter(item.value);
            }}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>
      {error ? <p className="error-banner">{error}</p> : null}
      {loading ? <p className="loading-card">正在读取 Memory Ledger…</p> : null}

      <section className="memory-list">
        {memories.map((memory) => (
          <article className="memory-card" key={memory.id}>
            <div className="memory-card-head">
              <div>
                <span className={`memory-status status-${memory.status}`}>
                  {memory.status}
                </span>
                <span className="memory-kind">{memory.kind}</span>
              </div>
              <small>{new Date(memory.updated_at).toLocaleString("zh-CN")}</small>
            </div>
            <h2>{memory.content}</h2>
            <dl className="memory-metrics">
              <div><dt>Confidence</dt><dd>{Math.round(memory.confidence * 100)}%</dd></div>
              <div><dt>Importance</dt><dd>{Math.round(memory.importance * 100)}%</dd></div>
              <div><dt>Trust</dt><dd>{memory.trust}</dd></div>
              <div><dt>Scope</dt><dd>{memory.knowledge_node_id ? "node" : memory.goal_id ? "goal" : "user"}</dd></div>
            </dl>
            <details className="memory-evidence">
              <summary>来源证据与 Revision（{memory.evidence.length} / {memory.revisions.length}）</summary>
              {memory.evidence.map((item) => (
                <p key={item.id}>
                  <strong>{item.source_type}</strong> · {item.source_id}<br />
                  <span>{item.excerpt}</span>
                </p>
              ))}
            </details>
            <div className="memory-actions">
              {memory.status === "candidate" ? (
                <>
                  <button onClick={() => void mutate(() => approveMemory(memory.id))} type="button">批准</button>
                  <button className="secondary-button" onClick={() => void mutate(() => rejectMemory(memory.id))} type="button">拒绝</button>
                </>
              ) : null}
              {memory.status === "active" ? (
                <button className="secondary-button" onClick={() => void mutate(() => deactivateMemory(memory.id))} type="button">停用</button>
              ) : null}
              <button className="text-button" onClick={() => { setEditing(memory); setContent(memory.content); setReason(""); }} type="button">纠正</button>
              <button className="danger-button" onClick={() => void mutate(() => deleteMemory(memory.id))} type="button">删除</button>
            </div>
          </article>
        ))}
        {!loading && memories.length === 0 ? (
          <p className="loading-card">这个状态下没有 Memory。Agent 会明确返回空集合，而不是补写事实。</p>
        ) : null}
      </section>

      {editing ? (
        <section className="memory-editor" aria-label="纠正 Memory">
          <h2>纠正 Memory</h2>
          <label>正确内容<textarea onChange={(event) => setContent(event.target.value)} value={content} /></label>
          <label>更正原因<input onChange={(event) => setReason(event.target.value)} value={reason} /></label>
          <div className="memory-actions">
            <button disabled={!content.trim() || !reason.trim()} onClick={() => void mutate(async () => { await correctMemory(editing.id, content, reason); setEditing(null); })} type="button">提交候选</button>
            <button className="secondary-button" onClick={() => setEditing(null)} type="button">取消</button>
          </div>
          <p className="muted-copy">纠正会创建带 supersedes 关系的新候选；批准前旧 Active Memory 继续有效。</p>
        </section>
      ) : null}
    </main>
  );
}
