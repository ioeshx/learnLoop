"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchAgentSkills,
  reviewAgentSkill,
  updateAgentSkillStatus,
  type SkillRecord,
} from "@/lib/api";

export default function AgentSkillsPage() {
  const [skills, setSkills] = useState<SkillRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = () =>
    fetchAgentSkills()
      .then(setSkills)
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : "读取 Skill Library 失败"),
      );

  useEffect(() => {
    void load();
  }, []);

  async function mutate(
    skill: SkillRecord,
    action: "publish" | "reject" | "disable" | "quarantine" | "revalidate",
  ) {
    const note = window.prompt("请输入审核原因（将进入 Audit metadata）");
    if (!note?.trim()) return;
    setBusyId(skill.id);
    setError(null);
    try {
      if (action === "publish" || action === "reject") {
        await reviewAgentSkill(skill.id, action, skill.version, note);
      } else {
        const target =
          action === "disable"
            ? "disabled"
            : action === "quarantine"
              ? "quarantined"
              : "candidate";
        await updateAgentSkillStatus(skill.id, target, skill.version, note);
      }
      await load();
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "Skill 状态更新失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/dashboard">← 返回仪表盘</Link>
        <span>Agent Skill Library</span>
      </nav>
      <p className="eyebrow">STAGE 16 · HUMAN REVIEW GATE</p>
      <h1 className="page-title">Verified Procedures</h1>
      <p className="page-subtitle">
        Candidate 默认不可执行；发布、隔离与停用均不改变原始版本和来源 Run。
      </p>
      {error ? <p className="error-banner">{error}</p> : null}
      {!skills.length && !error ? <p className="loading-card">暂无 Skill。</p> : null}
      {skills.map((skill) => {
        const uses = skill.success_count + skill.failure_count;
        const rate = uses ? `${((skill.success_count / uses) * 100).toFixed(0)}%` : "—";
        return (
          <section className="dashboard-card" key={skill.id}>
            <p className="eyebrow">
              {skill.status} · v{skill.version} · risk {skill.risk}
            </p>
            <h2>{skill.name}</h2>
            <p>{skill.description}</p>
            <div className="metric-grid compact-metrics">
              <Metric label="来源 Runs" value={skill.source_run_ids.length} />
              <Metric label="成功率" value={rate} />
              <Metric label="平均 Tool" value={skill.average_tool_calls.toFixed(1)} />
              <Metric label="平均 Token" value={skill.average_tokens.toFixed(0)} />
            </div>
            <p className="page-subtitle">
              Match：{skill.applicability.objective_keywords.join("、")} · Required Tools：
              {skill.applicability.required_tools.join("、") || "无"}
            </p>
            {skill.steps.map((step) => (
              <div className="trace-row" key={step.order}>
                <strong>{step.order}. {step.instruction}</strong>
                <span>Verifier: {step.verifier}</span>
                <small>Tools: {step.allowed_tools.join("、") || "无"}</small>
              </div>
            ))}
            <p className="page-subtitle">
              Provenance：
              {skill.source_run_ids.map((runId, index) => (
                <span key={runId}>
                  {index ? "、" : ""}
                  <Link href={`/agent-runs/${runId}`}>{runId}</Link>
                </span>
              ))}
            </p>
            <div className="agent-actions">
              {skill.status === "candidate" ? (
                <>
                  <button disabled={busyId === skill.id} onClick={() => void mutate(skill, "publish")}>
                    发布此版本
                  </button>
                  <button className="secondary-button" disabled={busyId === skill.id} onClick={() => void mutate(skill, "reject")}>
                    拒绝
                  </button>
                </>
              ) : null}
              {skill.status === "active" ? (
                <>
                  <button className="secondary-button" disabled={busyId === skill.id} onClick={() => void mutate(skill, "disable")}>
                    全局停用
                  </button>
                  <button className="secondary-button" disabled={busyId === skill.id} onClick={() => void mutate(skill, "quarantine")}>
                    隔离复验
                  </button>
                </>
              ) : null}
              {skill.status === "quarantined" || skill.status === "disabled" ? (
                <button disabled={busyId === skill.id} onClick={() => void mutate(skill, "revalidate")}>
                  返回 Candidate
                </button>
              ) : null}
            </div>
          </section>
        );
      })}
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
