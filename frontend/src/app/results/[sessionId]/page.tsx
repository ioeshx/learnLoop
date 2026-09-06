"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import {
  completeStudySession,
  fetchStudySession,
  type StudySession,
} from "@/lib/api";

export default function ResultPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<StudySession | null>(null);
  const [completing, setCompleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchStudySession(sessionId)
      .then((value) => active && setSession(value))
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "读取作答结果失败");
        }
      });
    return () => {
      active = false;
    };
  }, [sessionId]);

  async function complete() {
    setCompleting(true);
    setError(null);
    try {
      setSession(await completeStudySession(sessionId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "完成学习失败");
    } finally {
      setCompleting(false);
    }
  }

  const result = session?.latest_result;

  return (
    <main className="page-shell result-shell">
      {error ? <p className="error-banner">{error}</p> : null}
      {!session && !error ? <p className="loading-card">正在读取结果…</p> : null}

      {session && !result ? (
        <section className="result-card">
          <h1>还没有找到作答结果</h1>
          <Link className="primary-button button-link" href={`/study-sessions/${session.id}`}>
            返回练习
          </Link>
        </section>
      ) : null}

      {session && result ? (
        <section className="result-card">
          <div className={`result-mark ${result.is_correct ? "correct" : "wrong"}`}>
            {result.is_correct ? "✓" : "×"}
          </div>
          <p className="eyebrow">LEARN · CHECK · ADAPT</p>
          <h1>{result.is_correct ? "回答正确" : "这次还没有答对"}</h1>
          <p className="result-summary">
            {result.is_correct
              ? "你的理解已经转化为一条掌握度证据。"
              : "错误也会进入学习记录，帮助后续调整练习。"}
          </p>

          <dl className="result-metrics">
            <div>
              <dt>本次得分</dt>
              <dd>{result.score.toFixed(1)}</dd>
            </div>
            <div>
              <dt>当前掌握度</dt>
              <dd>{Math.round(result.mastery_score * 100)}%</dd>
            </div>
            <div>
              <dt>下次复习</dt>
              <dd>{new Date(result.due_at).toLocaleString("zh-CN")}</dd>
            </div>
          </dl>

          {!result.is_correct ? (
            <div className="answer-review">
              <span>参考答案</span>
              <strong>{result.expected_answer.join("、")}</strong>
            </div>
          ) : null}

          <div className="result-actions">
            {session.status !== "completed" ? (
              <button
                className="primary-button"
                disabled={completing}
                onClick={complete}
                type="button"
              >
                {completing ? "正在保存…" : "完成本阶段"}
              </button>
            ) : (
              <span className="completion-note">本阶段已完成并保存</span>
            )}
            <Link className="text-link" href={`/plans/${session.plan_id}`}>
              返回学习计划 →
            </Link>
          </div>
        </section>
      ) : null}
    </main>
  );
}
