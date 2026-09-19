"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchTeamArtifacts,
  fetchTeamRoles,
  fetchTeamTasks,
  type AgentCard,
  type TeamArtifact,
  type TeamTask,
} from "@/lib/api";

export default function AgentTeamPage() {
  const [roles, setRoles] = useState<AgentCard[]>([]);
  const [tasks, setTasks] = useState<TeamTask[]>([]);
  const [artifacts, setArtifacts] = useState<TeamArtifact[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetchTeamRoles(), fetchTeamTasks(), fetchTeamArtifacts()])
      .then(([nextRoles, nextTasks, nextArtifacts]) => {
        setRoles(nextRoles);
        setTasks(nextTasks);
        setArtifacts(nextArtifacts);
      })
      .catch((caught: unknown) =>
        setError(
          caught instanceof Error ? caught.message : "读取 Agent Team 失败",
        ),
      );
  }, []);

  const failed = tasks.filter((item) => item.status === "failed").length;
  const verified = artifacts.filter((item) => item.verified).length;

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/dashboard">← 返回仪表盘</Link>
        <span>Agent Team</span>
      </nav>
      <p className="eyebrow">V3 · POLICY-BOUNDED MULTI-AGENT</p>
      <h1 className="page-title">Roles, Tasks & Verified Artifacts</h1>
      <p className="page-subtitle">
        Agent Card 只声明 capability；Task authority 来自独立 Grant，Artifact 必须通过 hash、lineage、Policy 与 Lead-side Verifier。
      </p>
      {error ? <p className="error-banner">{error}</p> : null}

      <section className="metric-grid compact-metrics">
        <Metric label="Roles" value={roles.length} />
        <Metric label="Tasks" value={tasks.length} />
        <Metric label="Failed" value={failed} />
        <Metric label="Verified" value={verified} />
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">TRUSTED ROLE REGISTRY</p>
        <h2>Agent Cards</h2>
        {roles.map((role) => (
          <div className="trace-row" key={role.role_id}>
            <strong>
              {role.role_id}@{role.version} · {role.risk}
            </strong>
            <span>{role.capabilities.join(", ")}</span>
            <small>
              tools {role.allowed_tools.join(", ") || "none"}
              {" · "}{role.read_only ? "read-only" : "side-effect"}
            </small>
          </div>
        ))}
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">TASK DAG</p>
        <h2>Recent Team Tasks</h2>
        {tasks.length === 0 ? <p className="empty-state">尚无 Team Task。</p> : null}
        {tasks.map((task) => (
          <div className="trace-row" key={task.id}>
            <strong>
              {task.status} · {task.role_id} · {task.task_key}
            </strong>
            <span>{task.objective}</span>
            <small>
              deps {task.dependency_keys.join(", ") || "root"}
              {" · "}tokens {task.used_tokens}/{task.allocated_tokens}
            </small>
          </div>
        ))}
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">VERIFIED FAN-IN</p>
        <h2>Artifacts</h2>
        {artifacts.map((artifact) => (
          <div className="trace-row" key={artifact.id}>
            <strong>
              {artifact.verified ? "verified" : "unverified"} · {artifact.role_id}
            </strong>
            <span>task {artifact.task_id}</span>
            <small>
              sha256 {artifact.sha256.slice(0, 16)} · labels {artifact.labels.length}
            </small>
          </div>
        ))}
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}
