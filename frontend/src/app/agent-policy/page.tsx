"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchAgentPolicyDecisions,
  type AgentPolicyDecision,
} from "@/lib/api";

export default function AgentPolicyPage() {
  const [decisions, setDecisions] = useState<AgentPolicyDecision[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchAgentPolicyDecisions()
      .then(setDecisions)
      .catch((caught: unknown) =>
        setError(
          caught instanceof Error ? caught.message : "读取 Agent Policy 失败",
        ),
      );
  }, []);

  const denied = decisions.filter((item) => item.effect === "deny");
  const approvals = decisions.filter(
    (item) => item.effect === "require_approval",
  );
  const uniqueRuns = new Set(
    decisions.map((item) => item.run_id).filter(Boolean),
  ).size;

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/dashboard">← 返回仪表盘</Link>
        <span>Agent Policy Console</span>
      </nav>
      <p className="eyebrow">V3 · TRUSTED AGENT CONTROL PLANE</p>
      <h1 className="page-title">Authorization & Data Lineage</h1>
      <p className="page-subtitle">
        展示 metadata-only Policy decisions；不记录 Tool arguments、Secret 或 hidden reasoning。
      </p>
      {error ? <p className="error-banner">{error}</p> : null}

      <section className="metric-grid compact-metrics">
        <Metric label="Decisions" value={decisions.length} />
        <Metric label="Denied" value={denied.length} />
        <Metric label="Approval" value={approvals.length} />
        <Metric label="Runs" value={uniqueRuns} />
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">POLICY AUDIT</p>
        <h2>Recent Decisions</h2>
        {decisions.length === 0 ? (
          <p className="empty-state">尚无 Agent Policy decision。</p>
        ) : null}
        {decisions.map((decision) => (
          <div className="trace-row" key={decision.id}>
            <strong>
              {decision.effect} · {decision.capability}
            </strong>
            <span>
              {decision.reason} · {decision.action}
            </span>
            <small>
              {decision.run_id ? (
                <Link href={`/agent-runs/${decision.run_id}`}>
                  {decision.run_id}
                </Link>
              ) : (
                "simulation"
              )}
              {" · "}{decision.policy_version}
              {" · "}{decision.request_fingerprint.slice(0, 16)}
            </small>
          </div>
        ))}
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}
