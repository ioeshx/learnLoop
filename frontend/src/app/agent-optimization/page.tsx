"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchBanditDecisions,
  fetchFailureClusters,
  fetchOptimizationRewards,
  fetchPolicyExperiments,
  fetchPolicyVersions,
  type BanditDecision,
  type ExperimentReport,
  type FailureCluster,
  type PolicyVersion,
  type RewardRecord,
} from "@/lib/api";

export default function AgentOptimizationPage() {
  const [policies, setPolicies] = useState<PolicyVersion[]>([]);
  const [rewards, setRewards] = useState<RewardRecord[]>([]);
  const [decisions, setDecisions] = useState<BanditDecision[]>([]);
  const [experiments, setExperiments] = useState<ExperimentReport[]>([]);
  const [clusters, setClusters] = useState<FailureCluster[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetchPolicyVersions(),
      fetchOptimizationRewards(),
      fetchBanditDecisions(),
      fetchPolicyExperiments(),
      fetchFailureClusters(),
    ])
      .then(([policyItems, rewardItems, decisionItems, experimentItems, clusterItems]) => {
        setPolicies(policyItems);
        setRewards(rewardItems);
        setDecisions(decisionItems);
        setExperiments(experimentItems);
        setClusters(clusterItems);
      })
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : "读取策略优化状态失败"),
      );
  }, []);

  const mature = rewards.filter((item) => item.status === "mature");
  const unsafe = rewards.filter((item) => !item.hard_gate_passed);

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/dashboard">← 返回仪表盘</Link>
        <span>Agent Policy Lab</span>
      </nav>
      <p className="eyebrow">STAGE 17 · SHADOW OPTIMIZATION</p>
      <h1 className="page-title">Policy, Reward & Replay</h1>
      <p className="page-subtitle">
        只有 Mature、Safety-gated、Holdout 通过的轨迹才能进入训练或 Policy promotion。
      </p>
      {error ? <p className="error-banner">{error}</p> : null}
      <section className="metric-grid compact-metrics">
        <Metric label="Policy Versions" value={policies.length} />
        <Metric label="Bandit Decisions" value={decisions.length} />
        <Metric label="Mature Rewards" value={mature.length} />
        <Metric label="Safety Rejected" value={unsafe.length} />
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">CONTEXTUAL BANDIT</p>
        <h2>Teaching Policies</h2>
        {policies.map((policy) => (
          <div className="trace-row" key={policy.id}>
            <strong>
              {policy.family}@{policy.version} · {policy.status}
            </strong>
            <span>
              {policy.algorithm} · α {policy.alpha} · ε {policy.epsilon}
            </span>
            <small>
              Arms: {policy.arms.map((arm) => arm.id).join("、")} · Reward {policy.reward_version}
            </small>
          </div>
        ))}
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">DECOMPOSED REWARD</p>
        <h2>Learning Outcomes</h2>
        {rewards.slice(0, 20).map((reward) => (
          <div className="trace-row" key={reward.id}>
            <strong>
              <Link href={`/agent-runs/${reward.run_id}`}>{reward.run_id}</Link> · {reward.status}
            </strong>
            <span>
              score {reward.optimization_score?.toFixed(3) ?? "pending"} · gate {String(reward.hard_gate_passed)}
            </span>
            <small>
              retention {reward.components.delayed_retention ?? "—"} · transfer {reward.components.transfer ?? "—"}
            </small>
          </div>
        ))}
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">OFF-POLICY EVALUATION</p>
        <h2>Holdout Experiments</h2>
        {experiments.map((report) => (
          <div className="trace-row" key={report.manifest.id}>
            <strong>{report.manifest.name} · {report.promotable ? "promotable" : "rejected"}</strong>
            <span>
              SNIPS {report.snips_reward.toFixed(3)} · lift {report.reward_lift.toFixed(3)} · ESS {report.effective_sample_size.toFixed(1)}
            </span>
            <small>
              token ratio {report.token_ratio.toFixed(3)} · safety {report.safety_violations} · {report.split}
            </small>
          </div>
        ))}
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">FAILURE ANALYSIS</p>
        <h2>Evidence-bound Clusters</h2>
        {clusters.map((cluster) => (
          <div className="trace-row" key={cluster.signature}>
            <strong>{cluster.problem_category} · {cluster.count} Runs</strong>
            <span>{cluster.root_causes.join("；") || "No inferred root cause"}</span>
            <small>{cluster.evidence_references.length} Evidence references</small>
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
