import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchTeamTasks } from "@/lib/api";

describe("Agent Team API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("preserves dependencies, authority decision, and run filter", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            id: "task-1",
            task_key: "evaluate",
            role_id: "evaluator",
            dependency_keys: ["left", "right"],
            status: "completed",
            policy_decision_id: "decision-1",
          },
        ]),
      ),
    );

    const tasks = await fetchTeamTasks("run-1");

    expect(fetchMock.mock.calls[0][0]).toContain("parent_run_id=run-1");
    expect(tasks[0].dependency_keys).toEqual(["left", "right"]);
    expect(tasks[0].policy_decision_id).toBe("decision-1");
  });
});
