"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import {
  deferReview,
  fetchDueReviews,
  newIdempotencyKey,
  startReviewSession,
  type DueReview,
} from "@/lib/api";

export default function ReviewsPage() {
  const router = useRouter();
  const requestKeys = useRef(new Map<string, string>());
  const [reviews, setReviews] = useState<DueReview[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchDueReviews()
      .then((items) => {
        if (active) setReviews(items);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(
            caught instanceof Error ? caught.message : "读取复习队列失败",
          );
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function start(review: DueReview) {
    setBusyId(review.knowledge_node_id);
    setError(null);
    const key =
      requestKeys.current.get(review.knowledge_node_id) ?? newIdempotencyKey();
    requestKeys.current.set(review.knowledge_node_id, key);
    try {
      const session = await startReviewSession(review.knowledge_node_id, key);
      router.push(`/study-sessions/${session.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "开始复习失败");
      setBusyId(null);
    }
  }

  async function defer(review: DueReview) {
    setBusyId(review.knowledge_node_id);
    setError(null);
    try {
      await deferReview(review.knowledge_node_id);
      setReviews((current) =>
        current.filter(
          (item) => item.knowledge_node_id !== review.knowledge_node_id,
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "延期复习失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href="/">← 返回首页</Link>
        <span>自适应复习</span>
      </nav>

      <section>
        <p className="eyebrow">FSRS · PRIORITIZED REVIEW</p>
        <h1 className="page-title">今天该复习什么</h1>
        <p className="page-description">
          队列综合逾期时间、当前掌握度和知识点难度排序。进入复习后，Agent
          会按推荐难度生成一道新题。
        </p>
      </section>

      {error ? <p className="error-banner">{error}</p> : null}
      {loading ? <p className="loading-card">正在计算复习优先级…</p> : null}
      {!loading && reviews.length === 0 ? (
        <section className="review-empty">
          <h2>当前没有到期内容</h2>
          <p>完成新的学习阶段后，FSRS 会自动安排下一次复习。</p>
        </section>
      ) : null}

      <div className="review-list">
        {reviews.map((review, index) => (
          <article className="review-card" key={review.knowledge_node_id}>
            <div className="review-rank">{index + 1}</div>
            <div>
              <div className="item-meta">
                <span>优先级 {review.priority_score.toFixed(1)}</span>
                <span className="status-pill">
                  {review.overdue_days > 0
                    ? `逾期 ${review.overdue_days} 天`
                    : "今日到期"}
                </span>
              </div>
              <h2>{review.knowledge_node_title}</h2>
              <p>{review.reason}</p>
              <div className="review-actions">
                <button
                  className="primary-button"
                  disabled={busyId === review.knowledge_node_id}
                  onClick={() => void start(review)}
                  type="button"
                >
                  {busyId === review.knowledge_node_id ? "处理中…" : "开始复习"}
                </button>
                <button
                  className="secondary-button"
                  disabled={busyId === review.knowledge_node_id}
                  onClick={() => void defer(review)}
                  type="button"
                >
                  延后一天
                </button>
              </div>
            </div>
          </article>
        ))}
      </div>
    </main>
  );
}
