import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchAgentTrace } from "@/lib/api";

describe("Agent delegation trace API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("preserves child Runs and delegation budgets", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          run: { run_id: "lead-1" },
          events: [],
          tool_calls: [],
          model_calls: [],
          total_tokens: 0,
          total_model_duration_ms: 0,
          total_tool_duration_ms: 0,
          dynamic_state: null,
          plan_versions: [],
          context_snapshots: [],
          child_runs: [{ run_id: "child-1", graph: "researcher" }],
          delegations: [
            {
              request: {
                id: "delegation-1",
                role: "researcher",
                budget: { allocated_tokens: 1200 },
              },
              child_run_id: "child-1",
              status: "completed",
            },
          ],
        }),
      ),
    );

    const trace = await fetchAgentTrace("lead-1");

    expect(trace.child_runs[0].graph).toBe("researcher");
    expect(trace.delegations[0].request.budget.allocated_tokens).toBe(1200);
  });
});
