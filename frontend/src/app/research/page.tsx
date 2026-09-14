"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import { startResearch, type ResearchResult } from "@/lib/api";

export default function ResearchPage() {
  const [result, setResult] = useState<ResearchResult | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setWorking(true);
    setError(null);
    try {
      setResult(
        await startResearch(
          String(data.get("question") ?? ""),
          String(data.get("goal_id") ?? ""),
          String(data.get("knowledge_node_id") ?? "") || undefined,
        ),
      );
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Research 执行失败");
    } finally {
      setWorking(false);
    }
  }

  return (
    <main className="page-shell research-shell">
      <nav className="page-nav">
        <Link href="/dashboard">← 返回仪表盘</Link>
        <Link href="/resources">管理本地资料</Link>
      </nav>
      <p className="eyebrow">AGENTIC RAG · LOCAL EVIDENCE ONLY</p>
      <h1 className="page-title">Research Tutor</h1>
      <p className="page-description">
        Agent 会判断是否需要检索，将复杂问题拆成 SubQuestion，并在固定预算内持续查找证据；只有通过 Evidence Gate 和 Citation Verifier 的 Claim 才会进入答案。
      </p>

      <form className="research-form" onSubmit={submit}>
        <label>
          学习目标 ID
          <input name="goal_id" placeholder="必填，用于限制资料作用域" required />
        </label>
        <label>
          知识点 ID
          <input name="knowledge_node_id" placeholder="可选，进一步收窄资料" />
        </label>
        <label className="research-question">
          研究问题
          <textarea
            name="question"
            placeholder="例如：比较 BFS 与 DFS 的数据结构、复杂度和适用场景"
            required
          />
        </label>
        <button className="primary-button" disabled={working}>
          {working ? "正在执行 Gap-driven Loop…" : "开始研究"}
        </button>
      </form>
      {error ? <p className="error-banner">{error}</p> : null}

      {result ? (
        <>
          <section className="research-summary">
            <div>
              <p className="step-label">VERIFIED ANSWER</p>
              <h2>{result.status === "completed" ? "证据闭环完成" : "证据仍有缺口"}</h2>
            </div>
            <div className="research-badges">
              <span>{result.mode}</span>
              <span>{result.usage.rounds} rounds</span>
              <span>{result.usage.queries} queries</span>
              <span>{result.usage.sources} sources</span>
              <span>{result.usage.estimated_tokens} tokens</span>
            </div>
            <p className="research-answer">{result.answer}</p>
            {result.gaps.length ? (
              <div className="research-gaps">
                <strong>Unresolved gaps</strong>
                {result.gaps.map((gap) => <span key={gap}>{gap}</span>)}
              </div>
            ) : null}
          </section>

          <section className="research-grid">
            <article className="dashboard-card">
              <p className="step-label">ATOMIC CLAIMS</p>
              <h2>Claim → Citation</h2>
              {result.claims.map((claim) => (
                <div className="claim-row" key={claim.id}>
                  <span className={`evidence-verdict verdict-${claim.citation_status}`}>
                    {claim.citation_status}
                  </span>
                  <p>{claim.text}</p>
                  <small>{claim.included_in_answer ? "已进入答案" : "已被移除"}</small>
                  <div className="claim-citations">
                    {result.citations
                      .filter((citation) => citation.claim_id === claim.id)
                      .map((citation) => (
                        <a
                          href={`#evidence-${citation.evidence_id}`}
                          key={citation.id}
                        >
                          {citation.status} · {citation.chunk_id}
                        </a>
                      ))}
                  </div>
                </div>
              ))}
            </article>
            <article className="dashboard-card">
              <p className="step-label">EVIDENCE LEDGER</p>
              <h2>包含与淘汰的 Chunk</h2>
              {result.evidence.map((item) => (
                <details
                  className="evidence-row"
                  id={`evidence-${item.id}`}
                  key={item.id}
                >
                  <summary>
                    <span className={`evidence-verdict verdict-${item.grade.verdict}`}>
                      {item.grade.verdict}
                    </span>
                    {item.title} · {item.section ?? item.chunk_id}
                  </summary>
                  <p>{item.excerpt}</p>
                  <small>
                    relevance {item.grade.relevance.toFixed(2)} · quality {item.grade.source_quality.toFixed(2)} · coverage {item.grade.coverage.toFixed(2)}
                  </small>
                  <code>{item.content_sha256.slice(0, 16)}…</code>
                </details>
              ))}
            </article>
          </section>
          <p className="trace-reference">Research Trace: {result.trace_id}</p>
        </>
      ) : null}
    </main>
  );
}
