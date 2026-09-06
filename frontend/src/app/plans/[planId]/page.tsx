"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  fetchStudyPlan,
  newIdempotencyKey,
  startStudySession,
  type StudyPlan,
} from "@/lib/api";

export default function PlanPage() {
  const { planId } = useParams<{ planId: string }>();
  const router = useRouter();
  const requestKeys = useRef(new Map<string, string>());
  const [plan, setPlan] = useState<StudyPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [startingItem, setStartingItem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchStudyPlan(planId)
      .then((value) => active && setPlan(value))
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "读取计划失败");
        }
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [planId]);

  async function start(itemId: string) {
    if (!plan) return;
    setStartingItem(itemId);
    setError(null);
    const requestKey = requestKeys.current.get(itemId) ?? newIdempotencyKey();
    requestKeys.current.set(itemId, requestKey);
    try {
      const session = await startStudySession(plan.goal_id, itemId, requestKey);
      router.push(`/study-sessions/${session.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "开始学习失败");
      setStartingItem(null);
    }
  }

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/">← 新建目标</Link>
        <span>学习计划</span>
      </nav>

      {loading ? <p className="loading-card">正在读取学习计划…</p> : null}
      {error ? <p className="error-banner">{error}</p> : null}

      {plan ? (
        <section>
          <p className="eyebrow">PLAN · VERSION {plan.version}</p>
          <h1 className="page-title">三步完成一次学习闭环</h1>
          <p className="page-description">
            每个阶段都包含固定讲解和一道客观题。完成后会更新掌握度并生成复习时间。
          </p>

          <div className="timeline">
            {plan.items.map((item, index) => (
              <article className="timeline-item" key={item.id}>
                <div className="timeline-index">{index + 1}</div>
                <div className="timeline-content">
                  <div className="item-meta">
                    <span>{item.estimated_minutes} 分钟</span>
                    <span className={`status-pill status-${item.status}`}>
                      {statusLabel(item.status)}
                    </span>
                  </div>
                  <h2>{item.title}</h2>
                  <p>{item.description}</p>
                  <button
                    className="secondary-button"
                    disabled={
                      item.status === "completed" || startingItem === item.id
                    }
                    onClick={() => start(item.id)}
                    type="button"
                  >
                    {startingItem === item.id
                      ? "正在进入…"
                      : item.status === "active"
                        ? "继续学习"
                        : item.status === "completed"
                          ? "已完成"
                          : "开始这一阶段"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </main>
  );
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "待学习",
    active: "进行中",
    completed: "已完成",
    skipped: "已跳过",
  };
  return labels[status] ?? status;
}
