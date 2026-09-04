import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BackendStatus } from "@/components/backend-status";

describe("BackendStatus", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows service metadata when the backend is online", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "ok",
          service: "LearnLoop API",
          version: "0.1.0",
          environment: "test",
        }),
        { status: 200 },
      ),
    );

    render(<BackendStatus />);

    expect(
      await screen.findByText("LearnLoop API v0.1.0 已连接"),
    ).toBeInTheDocument();
  });

  it("shows a useful error instead of a blank page", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("connection refused"));

    render(<BackendStatus />);

    expect(
      await screen.findByText("后端未连接：connection refused"),
    ).toBeInTheDocument();
  });
});
