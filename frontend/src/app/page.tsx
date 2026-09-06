"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { BackendStatus } from "@/components/backend-status";
import {
  createGoal,
  createStudyPlan,
  newIdempotencyKey,
} from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const [requestKey] = useState(newIdempotencyKey);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const form = new FormData(event.currentTarget);

    try {
      const goal = await createGoal(
        {
          title: String(form.get("title") ?? ""),
          description: String(form.get("description") ?? ""),
          desired_outcome: String(form.get("desired_outcome") ?? ""),
          weekly_minutes: Number(form.get("weekly_minutes")),
          target_date: String(form.get("target_date") ?? "") || null,
        },
        requestKey,
      );
      const plan = await createStudyPlan(goal.id);
      router.push(`/plans/${plan.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "创建学习目标失败");
      setSubmitting(false);
    }
  }

  return (
    <main className="page-shell">
      <header className="topbar">
        <div className="brand">LearnLoop</div>
        <BackendStatus />
      </header>

      <section className="split-layout">
        <div className="intro-panel">
          <p className="eyebrow">DETERMINISTIC LEARNING LOOP</p>
          <h1>把目标变成一次可以完成的学习。</h1>
          <p className="subtitle">
            当前版本不调用 LLM。它会根据你的目标生成一套固定但完整的三阶段计划，
            让你体验目标、讲解、练习、掌握度和复习排期组成的闭环。
          </p>
          <ol className="flow-list">
            <li>定义你想获得的能力</li>
            <li>得到可执行的三阶段计划</li>
            <li>学习、作答并安排下一次复习</li>
          </ol>
        </div>

        <form className="form-card" onSubmit={handleSubmit}>
          <div>
            <p className="step-label">新学习目标</p>
            <h2>这一次，你想学会什么？</h2>
          </div>

          <label>
            主题
            <input
              name="title"
              placeholder="例如：图算法"
              maxLength={300}
              required
            />
          </label>

          <label>
            期望结果
            <textarea
              name="desired_outcome"
              placeholder="例如：能够独立实现 BFS 和 DFS"
              rows={3}
              required
            />
          </label>

          <label>
            补充说明
            <textarea
              name="description"
              placeholder="已有基础、偏好的学习方式等（可选）"
              rows={2}
            />
          </label>

          <div className="form-grid">
            <label>
              每周投入（分钟）
              <input
                name="weekly_minutes"
                type="number"
                min={1}
                max={10080}
                defaultValue={180}
                required
              />
            </label>
            <label>
              目标日期
              <input name="target_date" type="date" />
            </label>
          </div>

          {error ? <p className="error-banner">{error}</p> : null}
          <button className="primary-button" disabled={submitting} type="submit">
            {submitting ? "正在创建学习路径…" : "生成固定学习计划"}
          </button>
        </form>
      </section>
    </main>
  );
}
