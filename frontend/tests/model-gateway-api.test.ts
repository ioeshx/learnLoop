import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchModelRoutes } from "@/lib/api";

describe("Model Gateway API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("preserves route attempts, fallback count, and run filter", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            id: "route-1",
            outcome: "succeeded",
            selected_provider_id: "fallback",
            fallback_count: 1,
            attempts: [
              { provider_id: "primary", succeeded: false },
              { provider_id: "fallback", succeeded: true },
            ],
          },
        ]),
      ),
    );

    const routes = await fetchModelRoutes("run-1");

    expect(fetchMock.mock.calls[0][0]).toContain("run_id=run-1");
    expect(routes[0].fallback_count).toBe(1);
    expect(routes[0].attempts.map((item) => item.provider_id)).toEqual([
      "primary",
      "fallback",
    ]);
  });
});
