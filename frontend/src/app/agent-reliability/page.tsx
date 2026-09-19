"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchReliabilityReports,
  runReliabilitySuite,
  type ReliabilityReport,
  type ReliabilityRunInput,
} from "@/lib/api";

const FAULTS = [
  "timeout",
  "transient_tool",
  "malformed_model",
  "stale_artifact",
  "goal_shift",
  "prompt_injection",
] as const;

function contractSuite(): ReliabilityRunInput {
  return {
    base_seed: 21,
    trials_per_scenario: 5,
    environment: {
      runtime_version: "v3-stage-21",
      policy_version: "agent-trust-1.0.0",
      prompt_versions: { reliability: "1.0.0" },
      provider_profile_versions: { fixture: "1.0.0" },
      dataset_version: "reliability-console-1.0.0",
    },
    scenarios: [
      {
        id: "console.contract_recovery",
        version: "1.0.0",
        title: "Replayable six-fault contract suite",
        task_kind: "research",
        role: "lead",
        variant: "team",
        required_evidence_ids: ["evidence:fixture"],
        max_tokens: 2000,
        max_estimated_cost_usd: 0.1,
        fixture_only: true,
        faults: FAULTS.map((kind) => ({
          id: `console:${kind}`,
          kind,
          target: kind === "prompt_injection" ? "context.ingress" : "agent.step",
          probability: 1,
          occurrence: 1,
          recoverable: true,
          safety_critical: kind === "prompt_injection",
          parameters: {},
        })),
      },
    ],
  };
}

export default function AgentReliabilityPage() {
  const [reports, setReports] = useState<ReliabilityReport[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = () =>
    fetchReliabilityReports()
      .then(setReports)
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : "读取 Reliability Report 失败"),
      );

  useEffect(() => {
    void reload();
  }, []);

  const runFixture = async () => {
    setRunning(true);
    setError(null);
    try {
      const report = await runReliabilitySuite(contractSuite());
      setReports((current) => [report, ...current]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "运行 Reliability Suite 失败");
    } finally {
      setRunning(false);
    }
  };

  const latest = reports[0];
  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/dashboard">← 返回仪表盘</Link>
        <span>Agent Reliability</span>
      </nav>
      <p className="eyebrow">V3 · STOCHASTIC RELIABILITY LAB</p>
      <h1 className="page-title">Fault Injection & Pass-k</h1>
      <p className="page-subtitle">
        用固定 seed、可重放 Manifest、Safety hard gate 与多维 Slice 检验 Agent；内置套件是 Contract fixture，不代表生产 efficacy。
      </p>
      <button disabled={running} onClick={() => void runFixture()} type="button">
        {running ? "正在运行…" : "运行六类故障 Contract Suite"}
      </button>
      {error ? <p className="error-banner">{error}</p> : null}

      {latest ? (
        <>
          <section className="metric-grid compact-metrics">
            <Metric label="pass@k" value={latest.metrics.pass_at_k} />
            <Metric label="pass^k" value={latest.metrics.pass_power_k} />
            <Metric label="Recovery" value={latest.metrics.recovery_rate} />
            <Metric label="Safety" value={latest.metrics.safety_rate} />
          </section>
          <section className="dashboard-card">
            <p className="eyebrow">WORST-SLICE ANALYSIS</p>
            <h2>{latest.fixture_only ? "Fixture-only Report" : "Production Report"}</h2>
            {latest.slices.map((slice) => (
              <div className="trace-row" key={`${slice.dimension}:${slice.value}`}>
                <strong>{slice.dimension} · {slice.value}</strong>
                <span>pass {percent(slice.pass_rate)} · safety {percent(slice.safety_rate)}</span>
                <small>{slice.trials} trials · score {slice.average_score.toFixed(3)}</small>
              </div>
            ))}
          </section>
          <section className="dashboard-card">
            <p className="eyebrow">REPLAY MANIFESTS</p>
            <h2>Trial lineage</h2>
            {latest.trials.map((trial) => (
              <div className="trace-row" key={trial.manifest.manifest_hash}>
                <strong>
                  {trial.grade.passed ? "passed" : "failed"} · {trial.manifest.scenario_id} #{trial.manifest.trial_index}
                </strong>
                <span>seed {trial.manifest.seed}</span>
                <small>sha256 {trial.manifest.manifest_hash.slice(0, 20)}</small>
              </div>
            ))}
          </section>
        </>
      ) : <p className="empty-state">尚无 Reliability Report。</p>}
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <article className="metric-card"><span>{label}</span><strong>{percent(value)}</strong></article>;
}

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
