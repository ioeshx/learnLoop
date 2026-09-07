"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  fetchStudySession,
  replayAgentEvents,
  resumeAgentRun,
  startDailyAgentRun,
  type AgentEvent,
  type AgentStreamResult,
  type StudySession,
} from "@/lib/api";

type StreamState =
  | "connecting"
  | "running"
  | "reconnecting"
  | "awaiting_input"
  | "completed"
  | "failed";

const NODE_LABELS: Record<string, string> = {
  load_context: "加载学习上下文",
  select_concepts: "选择本轮知识点",
  retrieve_sources: "检索学习资料",
  generate_lesson: "生成讲解",
  generate_exercise: "准备练习",
  wait_for_answer: "等待作答",
  evaluate_answer: "评估答案",
  review_grade: "等待评分确认",
  route_by_result: "选择学习路径",
  update_mastery: "更新掌握度",
  schedule_review: "安排复习",
  diagnose_error: "诊断错误原因",
  generate_supplemental: "生成补充讲解",
  generate_prerequisite_remediation: "生成前置知识补救",
  save_summary: "保存学习总结",
};

export default function StudySessionPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const router = useRouter();
  const runIdRef = useRef<string | null>(null);
  const lastSequenceRef = useRef(0);
  const [session, setSession] = useState<StudySession | null>(null);
  const [selected, setSelected] = useState("");
  const [streamState, setStreamState] = useState<StreamState>("connecting");
  const [currentNode, setCurrentNode] = useState<string | null>(null);
  const [currentTool, setCurrentTool] = useState<string | null>(null);
  const [interrupt, setInterrupt] = useState<Record<string, unknown> | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAgentEvent = useCallback((event: AgentEvent) => {
    runIdRef.current = event.run_id;
    lastSequenceRef.current = Math.max(lastSequenceRef.current, event.sequence);
    if (event.event === "run_started") {
      setStreamState("running");
    } else if (event.event === "node_started") {
      setCurrentNode(event.node);
      setStreamState("running");
    } else if (event.event === "node_completed") {
      setCurrentNode(event.node);
    } else if (event.event === "tool_started") {
      setCurrentTool(event.node);
    } else if (event.event === "tool_completed") {
      setCurrentTool(null);
    } else if (event.event === "interrupt_created") {
      const value = event.data.value;
      const interruptValue = isRecord(value) ? value : {};
      setInterrupt(interruptValue);
      setCurrentNode(event.node);
      const previousSelection = stringList(interruptValue.selected_options);
      setSelected(
        interruptValue.type === "answer_required"
          ? ""
          : (previousSelection?.[0] ?? ""),
      );
      setStreamState("awaiting_input");
    } else if (event.event === "run_completed") {
      setInterrupt(null);
      setCurrentTool(null);
      setStreamState("completed");
    } else if (event.event === "run_failed") {
      const message = event.data.message;
      setError(typeof message === "string" ? message : "Agent 运行失败");
      setStreamState("failed");
    }
  }, []);

  const followStream = useCallback(
    async (
      initialRequest: () => Promise<AgentStreamResult>,
      signal: AbortSignal,
    ): Promise<AgentEvent | null> => {
      let result: AgentStreamResult;
      try {
        result = await initialRequest();
      } catch (caught) {
        if (signal.aborted) return null;
        if (!runIdRef.current) throw caught;
        result = { runId: runIdRef.current, lastEvent: null };
      }
      if (result.runId) runIdRef.current = result.runId;
      if (isBoundaryEvent(result.lastEvent)) return result.lastEvent;

      for (let attempt = 1; attempt <= 3; attempt += 1) {
        if (signal.aborted || !runIdRef.current) return null;
        setStreamState("reconnecting");
        await delay(250 * 2 ** (attempt - 1), signal);
        try {
          result = await replayAgentEvents(
            runIdRef.current,
            lastSequenceRef.current,
            handleAgentEvent,
            signal,
          );
          if (isBoundaryEvent(result.lastEvent)) return result.lastEvent;
        } catch (caught) {
          if (signal.aborted) return null;
          if (attempt === 3) throw caught;
        }
      }
      throw new Error("Agent 事件流已断开，请刷新页面恢复");
    },
    [handleAgentEvent],
  );

  const finishBoundary = useCallback(
    async (boundary: AgentEvent | null) => {
      if (boundary?.event !== "run_completed") return;
      const refreshed = await fetchStudySession(sessionId);
      setSession(refreshed);
      router.push(`/results/${sessionId}`);
    },
    [router, sessionId],
  );

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const value = await fetchStudySession(sessionId);
        if (controller.signal.aborted) return;
        if (value.latest_result) {
          router.replace(`/results/${sessionId}`);
          return;
        }
        setSession(value);
        const boundary = await followStream(
          () =>
            startDailyAgentRun(
              sessionId,
              handleAgentEvent,
              controller.signal,
            ),
          controller.signal,
        );
        if (!controller.signal.aborted) await finishBoundary(boundary);
      } catch (caught) {
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : "读取学习内容失败");
          setStreamState("failed");
        }
      }
    }
    void load();
    return () => controller.abort();
  }, [finishBoundary, followStream, handleAgentEvent, router, sessionId]);

  async function resume(value: Record<string, unknown>) {
    const runId = runIdRef.current;
    if (!runId) return;
    const controller = new AbortController();
    setSubmitting(true);
    setInterrupt(null);
    setError(null);
    setStreamState("running");
    try {
      const boundary = await followStream(
        () => resumeAgentRun(runId, value, handleAgentEvent, controller.signal),
        controller.signal,
      );
      await finishBoundary(boundary);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提交 Agent 输入失败");
      setStreamState("failed");
    } finally {
      setSubmitting(false);
    }
  }

  function submitAnswer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    void resume({ selected_options: [selected] });
  }

  const interruptType = stringValue(interrupt?.type);
  const exercise = isRecord(interrupt?.exercise) ? interrupt.exercise : null;
  const prompt = stringValue(exercise?.prompt) ?? session?.exercise.prompt;
  const options = stringList(exercise?.options) ?? session?.exercise.options ?? [];
  const evaluation = isRecord(interrupt?.evaluation) ? interrupt.evaluation : null;

  return (
    <main className="page-shell narrow-shell">
      <nav className="page-nav">
        <Link
          href={
            session?.kind === "review"
              ? "/reviews"
              : session
                ? `/plans/${session.plan_id}`
                : "/"
          }
        >
          ← {session?.kind === "review" ? "返回复习队列" : "返回计划"}
        </Link>
        <span>{session?.kind === "review" ? "Agent 复习会话" : "Agent 学习会话"}</span>
      </nav>

      <AgentProgress
        currentNode={currentNode}
        currentTool={currentTool}
        state={streamState}
      />
      {error ? <p className="error-banner">{error}</p> : null}
      {!session && !error ? (
        <p className="loading-card">正在准备本次学习…</p>
      ) : null}

      {session ? (
        <div className="learning-stack">
          <article className="lesson-card">
            <p className="eyebrow">
              {session.kind === "review" ? "RECALL · REVIEW" : "READ · UNDERSTAND"}
            </p>
            <h1 className="page-title">{session.lesson_title}</h1>
            <p className="lesson-copy">{session.lesson_content}</p>
            <aside className="learning-tip">
              Agent 会在作答和评分两个节点暂停；页面刷新后仍可从同一位置继续。
            </aside>
            <aside className="adaptation-card">
              <strong>
                自适应难度 {session.adaptation.base_difficulty.toFixed(1)} →{" "}
                {session.adaptation.target_difficulty.toFixed(1)}
              </strong>
              <p>{session.adaptation.reasons.join("；")}</p>
              {session.adaptation.prerequisite_gaps.length > 0 ? (
                <p>
                  前置缺口：
                  {session.adaptation.prerequisite_gaps
                    .map((gap) => `${gap.title} ${Math.round(gap.score * 100)}%`)
                    .join("、")}
                </p>
              ) : null}
            </aside>
          </article>

          {interruptType === "grade_review_required" && evaluation ? (
            <GradeReview
              evaluation={evaluation}
              onAccept={() => void resume({ action: "accept" })}
              onRevise={() =>
                void resume({
                  action: "revise_answer",
                  selected_options: [selected],
                })
              }
              options={options}
              selected={selected}
              setSelected={setSelected}
              submitting={submitting}
            />
          ) : (
            <form className="exercise-card" onSubmit={submitAnswer}>
              <p className="step-label">理解检查</p>
              <h2>{prompt}</h2>
              <div className="option-list">
                {options.map((option) => (
                  <label className="option" key={option}>
                    <input
                      checked={selected === option}
                      disabled={interruptType !== "answer_required" || submitting}
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
                disabled={
                  !selected || interruptType !== "answer_required" || submitting
                }
                type="submit"
              >
                {submitting ? "Agent 正在评估…" : "提交给 Agent"}
              </button>
            </form>
          )}
        </div>
      ) : null}
    </main>
  );
}

function AgentProgress({
  currentNode,
  currentTool,
  state,
}: {
  currentNode: string | null;
  currentTool: string | null;
  state: StreamState;
}) {
  const labels: Record<StreamState, string> = {
    connecting: "正在连接 Agent",
    running: "Agent 正在运行",
    reconnecting: "事件流重连中",
    awaiting_input: "等待你的输入",
    completed: "本轮已完成",
    failed: "运行失败",
  };
  return (
    <aside className="agent-progress" data-state={state}>
      <span className="agent-progress-dot" />
      <div>
        <strong>{labels[state]}</strong>
        <p>
          {currentNode
            ? `当前步骤：${NODE_LABELS[currentNode] ?? currentNode}`
            : "正在读取工作流状态"}
          {currentTool ? ` · 工具：${currentTool}` : ""}
        </p>
      </div>
    </aside>
  );
}

function GradeReview({
  evaluation,
  onAccept,
  onRevise,
  options,
  selected,
  setSelected,
  submitting,
}: {
  evaluation: Record<string, unknown>;
  onAccept: () => void;
  onRevise: () => void;
  options: string[];
  selected: string;
  setSelected: (value: string) => void;
  submitting: boolean;
}) {
  const correct = evaluation.is_correct === true;
  const score = typeof evaluation.score === "number" ? evaluation.score : 0;
  const expected = stringList(evaluation.expected_answer) ?? [];
  return (
    <section className="exercise-card grade-review-card">
      <p className="step-label">评分确认</p>
      <h2>{correct ? "Agent 判断本题正确" : "Agent 判断本题需要补救"}</h2>
      <p className="grade-summary">
        得分 {score}；参考答案：{expected.join("、") || "未提供"}。确认后才会写入掌握度，
        也可以更改选择并纠正评分。
      </p>
      <div className="option-list">
        {options.map((option) => (
          <label className="option" key={option}>
            <input
              checked={selected === option}
              disabled={submitting}
              name="corrected-answer"
              onChange={() => setSelected(option)}
              type="radio"
              value={option}
            />
            <span>{option}</span>
          </label>
        ))}
      </div>
      <div className="agent-actions">
        <button className="primary-button" disabled={submitting} onClick={onAccept}>
          接受评分
        </button>
        <button
          className="secondary-button"
          disabled={!selected || submitting}
          onClick={onRevise}
        >
          使用当前选择纠正
        </button>
      </div>
    </section>
  );
}

function isBoundaryEvent(event: AgentEvent | null): boolean {
  return (
    event?.event === "interrupt_created" ||
    event?.event === "run_completed" ||
    event?.event === "run_failed"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function stringList(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
    ? value
    : null;
}

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timeout = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
