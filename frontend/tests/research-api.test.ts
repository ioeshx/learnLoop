import { afterEach, describe, expect, it, vi } from "vitest";

import { startResearch } from "@/lib/api";

describe("Research Tutor API client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("starts a scoped research run", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          trace_id: "research-1",
          mode: "multi_step_research",
          status: "completed",
          answer: "BFS uses a queue [1]",
          claims: [],
          citations: [],
          evidence: [],
          gaps: [],
          usage: {
            rounds: 2,
            queries: 3,
            sources: 2,
            read_chars: 100,
            estimated_tokens: 34,
            stopped_reason: null,
          },
        }),
      ),
    );

    const result = await startResearch("比较 BFS 与 DFS", "goal-1", "node-1");

    expect(result.trace_id).toBe("research-1");
    expect(fetchMock.mock.calls[0][0]).toContain("/research/runs");
    expect(fetchMock.mock.calls[0][1]?.method).toBe("POST");
    expect(fetchMock.mock.calls[0][1]?.body).toBe(
      JSON.stringify({
        question: "比较 BFS 与 DFS",
        goal_id: "goal-1",
        knowledge_node_id: "node-1",
      }),
    );
  });
});
