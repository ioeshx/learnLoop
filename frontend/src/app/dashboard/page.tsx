"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  downloadLearningData,
  fetchAgentRuns,
  fetchDueReviews,
  fetchGoals,
  fetchLearningInsights,
  type AgentRun,
  type DueReview,
  type Goal,
  type LearningInsights,
} from "@/lib/api";

export default function DashboardPage() {
  const [goals, setGoals] = useState<Goal[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [reviews, setReviews] = useState<DueReview[]>([]);
  const [selectedGoalId, setSelectedGoalId] = useState("");
  const [insights, setInsights] = useState<LearningInsights | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    Promise.all([fetchGoals(), fetchAgentRuns(), fetchDueReviews()])
      .then(([goalItems, runItems, reviewItems]) => {
        if (!active) return;
        setGoals(goalItems);
        setRuns(runItems);
        setReviews(reviewItems);
        setSelectedGoalId(goalItems[0]?.id ?? "");
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "读取仪表盘失败");
        }
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedGoalId) {
      return;
    }
    let active = true;
    fetchLearningInsights(selectedGoalId)
      .then((value) => active && setInsights(value))
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "读取学习洞察失败");
        }
      });
    return () => {
      active = false;
    };
  }, [selectedGoalId]);

  const nodeById = new Map(insights?.nodes.map((node) => [node.id, node]) ?? []);
  const accuracy = insights?.weekly.attempts
    ? insights.weekly.correct_attempts / insights.weekly.attempts
    : 0;

  return (
    <main className="page-shell">
      <nav className="page-nav">
        <Link href="/">← 返回首页</Link>
        <div className="nav-actions">
          <Link href="/agent-skills">Agent Skills</Link>
          <Link href="/research">Research Tutor</Link>
          <Link href="/memories">Agent Memory</Link>
          <Link href="/reviews">今日复习</Link>
          <button
            className="text-button"
            onClick={() => void downloadLearningData()}
            type="button"
          >
            导出备份
          </button>
        </div>
      </nav>

      <p className="eyebrow">LEARNING OPERATIONS</p>
      <h1 className="page-title">学习仪表盘</h1>
      <p className="page-description">
        从知识依赖、掌握度、复习负债到 Agent Trace，查看一次学习决策的完整依据。
      </p>

      {error ? <p className="error-banner">{error}</p> : null}
      {loading ? <p className="loading-card">正在汇总本地学习数据…</p> : null}

      {goals.length > 0 ? (
        <label className="dashboard-goal-select">
          学习目标
          <select
            onChange={(event) => {
              setInsights(null);
              setSelectedGoalId(event.target.value);
            }}
            value={selectedGoalId}
          >
            {goals.map((goal) => (
              <option key={goal.id} value={goal.id}>
                {goal.title}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      {insights ? (
        <>
          <section className="metric-grid">
            <Metric label="近 7 天作答" value={String(insights.weekly.attempts)} />
            <Metric label="正确率" value={`${Math.round(accuracy * 100)}%`} />
            <Metric
              label="平均掌握度"
              value={`${Math.round(insights.weekly.average_mastery * 100)}%`}
            />
            <Metric label="到期复习" value={String(reviews.length)} />
          </section>

          <section className="dashboard-grid">
            <article className="dashboard-card">
              <p className="step-label">知识图谱</p>
              <h2>依赖关系</h2>
              <div className="knowledge-map">
                {insights.nodes.map((node) => (
                  <div className="knowledge-node" key={node.id}>
                    <span>{node.title}</span>
                    <strong>{Math.round(node.mastery_score * 100)}%</strong>
                    <div>
                      <i style={{ width: `${node.mastery_score * 100}%` }} />
                    </div>
                  </div>
                ))}
                {insights.edges.map((edge) => (
                  <p
                    className="knowledge-edge"
                    key={`${edge.source_node_id}-${edge.target_node_id}`}
                  >
                    {nodeById.get(edge.source_node_id)?.title ?? "未知"} →{" "}
                    {nodeById.get(edge.target_node_id)?.title ?? "未知"}
                  </p>
                ))}
              </div>
            </article>

            <article className="dashboard-card">
              <p className="step-label">掌握度趋势</p>
              <h2>最近事件</h2>
              <div className="trend-list">
                {insights.mastery_trend
                  .slice(-8)
                  .reverse()
                  .map((point) => (
                    <div
                      key={`${point.knowledge_node_id}-${point.occurred_at}`}
                    >
                      <span>{point.knowledge_node_title}</span>
                      <strong>{Math.round(point.score * 100)}%</strong>
                      <small>
                        {new Date(point.occurred_at).toLocaleString("zh-CN")}
                      </small>
                    </div>
                  ))}
                {insights.mastery_trend.length === 0 ? (
                  <p className="muted-copy">完成练习后会出现掌握度事件。</p>
                ) : null}
              </div>
            </article>
          </section>
        </>
      ) : null}

      <section className="dashboard-card trace-list-card">
        <p className="step-label">AGENT TRACE</p>
        <h2>最近运行</h2>
        <div className="trace-run-list">
          {runs.slice(0, 10).map((run) => (
            <Link href={`/agent-runs/${run.run_id}`} key={run.run_id}>
              <span>{run.graph}</span>
              <strong>{run.status}</strong>
              <small>{new Date(run.updated_at).toLocaleString("zh-CN")}</small>
            </Link>
          ))}
          {runs.length === 0 ? (
            <p className="muted-copy">还没有 Agent 运行记录。</p>
          ) : null}
        </div>
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}
