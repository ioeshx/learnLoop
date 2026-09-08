"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { fetchAgentTrace, type AgentTrace } from "@/lib/api";

export default function AgentTracePage() {
  const { runId } = useParams<{ runId: string }>();
  const [trace, setTrace] = useState<AgentTrace | null>(null);
  const [error, setError] = useState<string | null>(null);

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
          <p className="eyebrow">{trace.run.graph}</p>
          <h1 className="page-title">{trace.run.status}</h1>
          <section className="metric-grid compact-metrics">
            <Metric label="事件" value={trace.events.length} />
            <Metric label="Tool 调用" value={trace.tool_calls.length} />
            <Metric label="Token" value={trace.total_tokens} />
            <Metric
              label="模型耗时"
              value={`${trace.total_model_duration_ms.toFixed(0)} ms`}
            />
          </section>

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
