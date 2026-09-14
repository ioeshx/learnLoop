"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { fetchAgentTrace, type AgentTrace } from "@/lib/api";

export default function AgentTracePage() {
  const { runId } = useParams<{ runId: string }>();
  const [trace, setTrace] = useState<AgentTrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latestContext = trace?.context_snapshots.at(-1);

  useEffect(() => {
    let active = true;
    fetchAgentTrace(runId)
      .then((value) => active && setTrace(value))
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "读取 Trace 失败");
        }
      });
    return () => {
      active = false;
    };
  }, [runId]);

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/dashboard">← 返回仪表盘</Link>
        <span>Agent Trace</span>
      </nav>
      {error ? <p className="error-banner">{error}</p> : null}
      {!trace && !error ? <p className="loading-card">正在读取运行轨迹…</p> : null}
      {trace ? (
        <>
          <p className="eyebrow">
            {trace.run.graph} · {trace.run.engine_version} · attempt {trace.run.attempt_no}
          </p>
          <h1 className="page-title">{trace.run.status}</h1>
          {trace.run.terminal_reason ? (
            <p className="page-subtitle">终止原因：{trace.run.terminal_reason}</p>
          ) : null}
          {trace.run.parent_run_id ? (
            <p className="page-subtitle">
              Parent Run：
              <Link href={`/agent-runs/${trace.run.parent_run_id}`}>
                {trace.run.parent_run_id}
              </Link>
            </p>
          ) : null}
          <section className="metric-grid compact-metrics">
            <Metric label="事件" value={trace.events.length} />
            <Metric label="Tool 调用" value={trace.tool_calls.length} />
            <Metric label="Token" value={trace.total_tokens} />
            <Metric
              label="模型耗时"
              value={`${trace.total_model_duration_ms.toFixed(0)} ms`}
            />
          </section>

          {trace.dynamic_state ? (
            <section className="dashboard-card">
              <p className="eyebrow">Dynamic Agent Plan v{trace.dynamic_state.plan.version}</p>
              <h2>{trace.dynamic_state.plan.objective}</h2>
              <div className="metric-grid compact-metrics">
                <Metric
                  label="Loop Step"
                  value={`${trace.dynamic_state.usage.steps}/${trace.dynamic_state.budget.max_steps}`}
                />
                <Metric
                  label="Model Call"
                  value={`${trace.dynamic_state.usage.model_calls}/${trace.dynamic_state.budget.max_model_calls}`}
                />
                <Metric
                  label="Tool Call"
                  value={`${trace.dynamic_state.usage.tool_calls}/${trace.dynamic_state.budget.max_tool_calls}`}
                />
                <Metric
                  label="Replan"
                  value={`${trace.dynamic_state.usage.replans}/${trace.dynamic_state.budget.max_replans}`}
                />
                <Metric
                  label="Delegated Token"
                  value={trace.dynamic_state.usage.delegated_tokens ?? 0}
                />
              </div>
              {trace.dynamic_state.plan.steps.map((step) => (
                <div className="trace-row" key={step.id}>
                  <strong>{step.id} · {step.status}</strong>
                  <span>{step.objective}</span>
                  <small>
                    Tools: {step.allowed_tools.join("、") || "无"} · Evidence: {step.evidence_ids.length}
                  </small>
                </div>
              ))}
            </section>
          ) : null}

          {trace.delegations.length ? (
            <section className="dashboard-card">
              <p className="eyebrow">SUBAGENT-AS-TOOL</p>
              <h2>Delegation Tree</h2>
              {trace.delegations.map((delegation) => (
                <div className="trace-row" key={delegation.request.id}>
                  <strong>
                    {delegation.request.role} · {delegation.status}
                  </strong>
                  <span>{delegation.request.objective}</span>
                  <small>
                    Budget {delegation.result?.usage.used_tokens ?? 0}/
                    {delegation.request.budget.allocated_tokens} tokens · Queries{" "}
                    {delegation.result?.usage.queries ?? 0}/
                    {delegation.request.budget.max_queries}
                  </small>
                  <Link href={`/agent-runs/${delegation.child_run_id}`}>
                    查看 Child Run →
                  </Link>
                  {delegation.result?.unresolved_questions.length ? (
                    <small>
                      Unresolved：
                      {delegation.result.unresolved_questions.join("；")}
                    </small>
                  ) : null}
                </div>
              ))}
            </section>
          ) : null}

          {latestContext ? (
            <section className="dashboard-card">
              <p className="eyebrow">
                Context Engine · {trace.context_snapshots.length} Snapshots
              </p>
              <h2>
                {latestContext.purpose} · Plan v{latestContext.plan_version} · {latestContext.step_id}
              </h2>
              <div className="metric-grid compact-metrics">
                <Metric
                  label="Input Token"
                  value={`${latestContext.total_input_tokens}/${latestContext.input_token_limit}`}
                />
                <Metric
                  label="Output Reserve"
                  value={latestContext.reserved_output_tokens}
                />
                <Metric label="来源" value={latestContext.source_ids.length} />
                <Metric
                  label="裁剪来源"
                  value={latestContext.omitted_source_ids.length}
                />
              </div>
              <p className="page-subtitle">
                Tokenizer: {latestContext.tokenizer_name}
                {latestContext.exact_token_count ? "（exact）" : "（fallback estimate）"}
                {latestContext.token_delta === null
                  ? ""
                  : ` · Provider delta ${latestContext.token_delta}`}
              </p>
              {latestContext.partitions.map((partition) => (
                <div className="trace-row" key={partition.name}>
                  <strong>
                    {partition.name} · {partition.token_count} tokens
                  </strong>
                  <span>
                    priority {partition.priority} · {partition.item_count} items
                  </span>
                  <small>{partition.mandatory ? "mandatory" : "optional"}</small>
                </div>
              ))}
              <p className="page-subtitle">
                Tools: {latestContext.tool_names.join("、") || "无"}
                {latestContext.truncations.length
                  ? ` · Compaction: ${latestContext.truncations.map((item) => item.reason).join("、")}`
                  : ""}
                {latestContext.conflicts.length
                  ? ` · Conflicts: ${latestContext.conflicts.length}`
                  : ""}
              </p>
            </section>
          ) : null}

          <section className="trace-columns">
            <article className="dashboard-card">
              <h2>节点与事件</h2>
              {trace.events.map((event) => (
                <div className="trace-row" key={event.sequence}>
                  <strong>#{event.sequence} {event.event}</strong>
                  <span>{event.node ?? "run"}</span>
                  <small>{new Date(event.timestamp).toLocaleTimeString("zh-CN")}</small>
                </div>
              ))}
            </article>
            <article className="dashboard-card">
              <h2>Tool 与模型</h2>
              {trace.tool_calls.map((call) => (
                <div className="trace-row" key={call.call_id}>
                  <strong>{call.tool_name}</strong>
                  <span>{call.status} · {(call.duration_ms ?? 0).toFixed(1)} ms</span>
                  <small>{Object.keys(call.arguments).join("、") || "无参数"}</small>
                </div>
              ))}
              {trace.model_calls.map((call) => (
                <div className="trace-row model-row" key={call.call_id}>
                  <strong>{call.prompt_name}@{call.prompt_version}</strong>
                  <span>{call.model} · {call.total_tokens} tokens</span>
                  <small>{call.duration_ms.toFixed(1)} ms · {call.attempts} 次尝试</small>
                </div>
              ))}
            </article>
          </section>
        </>
      ) : null}
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
