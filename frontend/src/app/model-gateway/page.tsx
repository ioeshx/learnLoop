"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchModelGatewayHealth,
  fetchModelGatewayProfiles,
  fetchModelRoutes,
  type ModelProfile,
  type ModelRouteRecord,
  type ProviderHealth,
} from "@/lib/api";

export default function ModelGatewayPage() {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [health, setHealth] = useState<ProviderHealth[]>([]);
  const [routes, setRoutes] = useState<ModelRouteRecord[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      fetchModelGatewayProfiles(),
      fetchModelGatewayHealth(),
      fetchModelRoutes(),
    ])
      .then(([nextProfiles, nextHealth, nextRoutes]) => {
        setProfiles(nextProfiles);
        setHealth(nextHealth);
        setRoutes(nextRoutes);
      })
      .catch((caught: unknown) =>
        setError(
          caught instanceof Error ? caught.message : "读取 Model Gateway 失败",
        ),
      );
  }, []);

  const fallbackRoutes = routes.filter((item) => item.fallback_count > 0);
  const openCircuits = health.filter((item) => item.state !== "closed");

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/dashboard">← 返回仪表盘</Link>
        <span>Model Gateway</span>
      </nav>
      <p className="eyebrow">V3 · RESILIENT MODEL CONTROL PLANE</p>
      <h1 className="page-title">Routing, Fallback & Circuit Health</h1>
      <p className="page-subtitle">
        Capability、Context、Cost、Deadline 与 Residency 先完成 preflight，只有 retryable failure 才允许 compatible fallback。
      </p>
      {error ? <p className="error-banner">{error}</p> : null}

      <section className="metric-grid compact-metrics">
        <Metric label="Providers" value={profiles.length} />
        <Metric label="Routes" value={routes.length} />
        <Metric label="Fallbacks" value={fallbackRoutes.length} />
        <Metric label="Open Circuits" value={openCircuits.length} />
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">PROVIDER HEALTH</p>
        <h2>Capability Profiles</h2>
        {profiles.map((profile) => {
          const state = health.find(
            (item) => item.provider_id === profile.provider_id,
          );
          return (
            <div className="trace-row" key={profile.provider_id}>
              <strong>
                {profile.provider_id} · {state?.state ?? "unknown"}
              </strong>
              <span>
                {profile.model} · {profile.capabilities.join(", ")}
              </span>
              <small>
                context {profile.context_window} · output {profile.max_output_tokens}
                {" · "}residency {profile.data_residency}
              </small>
            </div>
          );
        })}
      </section>

      <section className="dashboard-card">
        <p className="eyebrow">ROUTE AUDIT</p>
        <h2>Recent Routes</h2>
        {routes.length === 0 ? (
          <p className="empty-state">尚无 Model route。</p>
        ) : null}
        {routes.map((route) => (
          <div className="trace-row" key={route.id}>
            <strong>
              {route.outcome} · {route.selected_provider_id ?? "no route"}
            </strong>
            <span>
              {route.prompt_name}@{route.prompt_version} · fallback {route.fallback_count}
            </span>
            <small>
              attempts {route.attempts.map((item) => item.provider_id).join(" → ") || "none"}
              {" · "}estimated ${route.estimated_cost_usd.toFixed(6)}
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
