import { afterEach, describe, expect, it, vi } from "vitest";

import {
  correctExerciseAttempt,
  createGoal,
  deferReview,
  replayAgentEvents,
  startDailyAgentRun,
  startReviewSession,
  startStudySession,
  submitExerciseAttempt,
} from "@/lib/api";

describe("learning workflow API client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("sends idempotency keys for mutating workflow requests", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "goal-1",
            user_id: "user-1",
            title: "Graphs",
            description: "",
            desired_outcome: "Implement BFS",
            weekly_minutes: 180,
            target_date: null,
            status: "draft",
            created_at: "2026-02-10T08:30:00Z",
            updated_at: "2026-02-10T08:30:00Z",
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: "session-1",
            goal_id: "goal-1",
            plan_id: "plan-1",
            plan_item_id: "item-1",
            status: "active",
            started_at: "2026-02-10T08:30:00Z",
            completed_at: null,
            lesson_title: "Basics",
            lesson_content: "Learn the basics",
            exercise: {
              id: "exercise-1",
              exercise_type: "multiple_choice",
              prompt: "Choose one",
              options: ["A", "B"],
              max_score: 1,
            },
            latest_result: null,
          }),
          { status: 201 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            attempt_id: "attempt-1",
            selected_options: ["A"],
            score: 1,
            is_correct: true,
            expected_answer: ["A"],
            mastery_score: 0.15,
            due_at: "2026-02-10T08:40:00Z",
            attempted_at: "2026-02-10T08:30:00Z",
          }),
          { status: 201 },
        ),
      );

    await createGoal(
      {
        title: "Graphs",
        description: "",
        desired_outcome: "Implement BFS",
        weekly_minutes: 180,
        target_date: null,
      },
      "goal-key",
    );
    await startStudySession("goal-1", "item-1", "session-key");
    await submitExerciseAttempt(
      "session-1",
      "exercise-1",
      ["A"],
      "attempt-key",
    );

    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({
      "Idempotency-Key": "goal-key",
    });
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({
      "Idempotency-Key": "session-key",
    });
    expect(fetchMock.mock.calls[2][1]?.headers).toMatchObject({
      "Idempotency-Key": "attempt-key",
    });
  });

  it("surfaces the backend error message", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ error: { message: "learning goal was not found" } }),
        { status: 404 },
      ),
    );

    await expect(
      startStudySession("missing", "item-1", "session-key"),
    ).rejects.toThrow("learning goal was not found");
  });

  it("calls adaptive review and correction endpoints", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            knowledge_node_id: "node-1",
            due_at: "2026-02-11T08:30:00Z",
            last_review_at: null,
          }),
          { status: 200 },
        ),
    );

    await startReviewSession("node-1", "review-key");
    await deferReview("node-1", 2);
    await correctExerciseAttempt("session-1", "attempt-1", ["A"]);

    expect(fetchMock.mock.calls[0][0]).toContain("/reviews/sessions");
    expect(fetchMock.mock.calls[0][1]?.headers).toMatchObject({
      "Idempotency-Key": "review-key",
    });
    expect(fetchMock.mock.calls[1][0]).toContain("/reviews/node-1/defer");
    expect(fetchMock.mock.calls[1][1]?.body).toBe(JSON.stringify({ days: 2 }));
    expect(fetchMock.mock.calls[2][0]).toContain(
      "/study-sessions/session-1/attempts/attempt-1",
    );
    expect(fetchMock.mock.calls[2][1]?.method).toBe("PATCH");
  });

  it("parses Agent SSE events and sends a replay cursor", async () => {
    const payload = {
      run_id: "run-1",
      sequence: 7,
      event: "interrupt_created",
      node: "wait_for_answer",
      timestamp: "2026-02-10T08:30:00Z",
      data: { value: { type: "answer_required" } },
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        new Response(
          `: keep-alive\n\nid: 7\nevent: interrupt_created\ndata: ${JSON.stringify(payload)}\n\n`,
          {
            status: 200,
            headers: {
              "Content-Type": "text/event-stream",
              "X-Agent-Run-Id": "run-1",
            },
          },
        ),
      );
    const received: unknown[] = [];

    const result = await startDailyAgentRun("session-1", (event) => {
      received.push(event);
    });
    await replayAgentEvents("run-1", 7, () => undefined);

    expect(result.runId).toBe("run-1");
    expect(result.lastEvent).toMatchObject(payload);
    expect(received).toEqual([payload]);
    expect(fetchMock.mock.calls[1][0]).toContain("?after=7");
    expect(fetchMock.mock.calls[1][1]?.headers).toMatchObject({
      "Last-Event-ID": "7",
    });
  });
});
