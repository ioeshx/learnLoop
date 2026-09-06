"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";

import {
  fetchStudySession,
  newIdempotencyKey,
  submitExerciseAttempt,
  type StudySession,
} from "@/lib/api";

export default function StudySessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const router = useRouter();
  const [requestKey] = useState(newIdempotencyKey);
  const [session, setSession] = useState<StudySession | null>(null);
  const [selected, setSelected] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchStudySession(sessionId)
      .then((value) => {
        if (!active) return;
        if (value.latest_result) {
          router.replace(`/results/${sessionId}`);
          return;
        }
        setSession(value);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "读取学习内容失败");
        }
      });
    return () => {
      active = false;
    };
  }, [router, sessionId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !selected) return;
    setSubmitting(true);
    setError(null);
    try {
      await submitExerciseAttempt(
        session.id,
        session.exercise.id,
        [selected],
        requestKey,
      );
      router.push(`/results/${session.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提交答案失败");
      setSubmitting(false);
    }
  }

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link href={session ? `/plans/${session.plan_id}` : "/"}>← 返回计划</Link>
        <span>学习中</span>
      </nav>

      {error ? <p className="error-banner">{error}</p> : null}
      {!session && !error ? (
        <p className="loading-card">正在准备本次学习…</p>
      ) : null}

      {session ? (
        <div className="learning-stack">
          <article className="lesson-card">
            <p className="eyebrow">READ · UNDERSTAND</p>
            <h1 className="page-title">{session.lesson_title}</h1>
            <p className="lesson-copy">{session.lesson_content}</p>
            <aside className="learning-tip">
              阅读后先尝试用自己的话复述，再完成下面的检查题。
            </aside>
          </article>

          <form className="exercise-card" onSubmit={submit}>
            <p className="step-label">理解检查</p>
            <h2>{session.exercise.prompt}</h2>
            <div className="option-list">
              {session.exercise.options.map((option) => (
                <label className="option" key={option}>
                  <input
                    checked={selected === option}
                    name="answer"
                    onChange={() => setSelected(option)}
                    type="radio"
                    value={option}
                  />
                  <span>{option}</span>
                </label>
              ))}
            </div>
            <button
              className="primary-button"
              disabled={!selected || submitting}
              type="submit"
            >
              {submitting ? "正在判分…" : "提交答案"}
            </button>
          </form>
        </div>
      ) : null}
    </main>
  );
}
