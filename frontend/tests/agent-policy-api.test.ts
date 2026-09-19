import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchAgentPolicyDecisions } from "@/lib/api";

describe("Agent Policy API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("preserves effect, reason, fingerprint, and run filter", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            id: "decision-1",
            effect: "deny",
            reason: "injection_to_side_effect",
            request_fingerprint: "a".repeat(64),
            run_id: "run-1",
            input_label_ids: ["label-1"],
          },
        ]),
      ),
    );

    const decisions = await fetchAgentPolicyDecisions("run-1");

    expect(fetchMock.mock.calls[0][0]).toContain("run_id=run-1");
    expect(decisions[0].effect).toBe("deny");
    expect(decisions[0].reason).toBe("injection_to_side_effect");
    expect(decisions[0].input_label_ids).toEqual(["label-1"]);
  });
});
