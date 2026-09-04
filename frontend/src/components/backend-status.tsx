"use client";

import { useEffect, useState } from "react";

import { fetchHealth, type HealthResponse } from "@/lib/api";

type Status =
  | { state: "loading" }
  | { state: "online"; health: HealthResponse }
  | { state: "offline"; message: string };

export function BackendStatus() {
  const [status, setStatus] = useState<Status>({ state: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    fetchHealth(controller.signal)
      .then((health) => setStatus({ state: "online", health }))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setStatus({
            state: "offline",
            message: error instanceof Error ? error.message : "Unknown error",
          });
        }
      });

    return () => controller.abort();
  }, []);

  if (status.state === "loading") {
    return (
      <div className="status" data-state="loading" role="status">
        <span className="status-dot" aria-hidden="true" />
        正在检查本地服务…
      </div>
    );
  }

  if (status.state === "offline") {
    return (
      <div className="status" data-state="offline" role="status">
        <span className="status-dot" aria-hidden="true" />
        <span>后端未连接：{status.message}</span>
      </div>
    );
  }

  return (
    <div className="status" data-state="online" role="status">
      <span className="status-dot" aria-hidden="true" />
      <span>
        {status.health.service} v{status.health.version} 已连接
      </span>
    </div>
  );
}
