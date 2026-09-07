import { afterEach, describe, expect, it, vi } from "vitest";

import { searchResources, uploadResource } from "@/lib/api";

describe("resource API client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uploads multipart data without overriding the browser boundary", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          resource: {
            id: "resource-1",
            goal_id: "goal-1",
            knowledge_node_id: "node-1",
            title: "notes",
            source_type: "file",
            source_uri: null,
            original_filename: "notes.md",
            media_type: "text/markdown",
            sha256: "abc",
            size_bytes: 12,
            status: "processing",
            error: null,
            created_at: "2026-09-07T00:00:00Z",
            updated_at: "2026-09-07T00:00:00Z",
          },
          job: { id: "job-1", status: "queued" },
        }),
        { status: 202 },
      ),
    );

    const submission = await uploadResource(
      new File(["# BFS"], "notes.md", { type: "text/markdown" }),
      "goal-1",
      "node-1",
    );

    const request = fetchMock.mock.calls[0][1];
    expect(submission.resource.id).toBe("resource-1");
    expect(submission.job.id).toBe("job-1");
    expect(request?.body).toBeInstanceOf(FormData);
    expect(request?.headers).toBeUndefined();
  });

  it("encodes hybrid-search filters", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("[]", { status: 200 }));

    await searchResources("BFS 队列", "goal/1", "node 1");

    expect(fetchMock.mock.calls[0][0]).toContain(
      "query=BFS+%E9%98%9F%E5%88%97&goal_id=goal%2F1&knowledge_node_id=node+1",
    );
  });
});
