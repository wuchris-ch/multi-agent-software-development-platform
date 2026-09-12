import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import type { Run, Publication } from "./types";

const id = "a".repeat(64),
  candidate = "b".repeat(64),
  planId = "d".repeat(64);
const fixture: Run = {
  id,
  key: "retry-task",
  title: "Fix HTTP retry delays",
  repository: "retry-helper",
  state: "ready_local",
  created_at: 1700000000,
  updated_at: 1700000060,
  request_budget: 30,
  requests_used_or_reserved: 7,
  repairs_used: 1,
  candidate_sha256: candidate,
  revision: "revision",
  task: "Fix HTTP retry delays without changing the public tests.",
  base_revision: "c".repeat(40),
  deadline: 1700003600,
  active: null,
  max_repairs: 2,
  can_cancel: false,
  stages: [],
  events: [
    { seq: 1, at: 1700000000, event: "workflow.created" },
    { seq: 2, at: 1700000060, event: "workflow.ready" },
  ],
  candidates: [candidate],
  candidate: {
    sha256: candidate,
    state: "ready_local",
    changed_paths: ["retry.py"],
    patch_sha256: "e".repeat(64),
    verification: { passed: true, exit_code: 0, reason: null },
    check_output: "15 tests passed",
    review: {
      blocked: false,
      risk: "low",
      rationale: "The capped retry behavior is correct.",
      findings: [],
    },
  },
  publications: [],
};
const publication: Publication = {
  plan_sha256: planId,
  state: "prepared",
  events: [],
  plan: {
    repository: "owner/retry-helper",
    branch: "development/retry-task",
    base_branch: "main",
    base_sha: "c".repeat(40),
    head_sha: "f".repeat(40),
    candidate_sha256: candidate,
    title: "Fix HTTP retry delays",
    body: "Fix retry behavior.",
    created_at: 1700000061,
    expires_at: 9999999999,
  },
};
let role = "operator";
let run: Run;
let requests: { path: string; init: RequestInit }[];
beforeEach(() => {
  role = "operator";
  run = structuredClone(fixture);
  requests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      requests.push({ path, init });
      let result: unknown;
      if (path.endsWith("/session")) result = { role };
      else if (path === "/api/workflows")
        result = { items: [run], unavailable: 0 };
      else if (path === `/api/workflows/${id}`) result = run;
      else if (path.includes("/diff?"))
        result = {
          diff: 'diff --git a/retry.py b/retry.py\n@@ -1 +1 @@\n-delay = 0\n+delay = "<img src=x onerror=alert(1)>"\n',
        };
      else if (path.endsWith("/publications")) result = publication;
      else if (path.endsWith("/publish"))
        result = {
          ...publication,
          state: "published",
          pull_request: {
            url: "https://github.com/owner/retry-helper/pull/1",
            number: 1,
            head_sha: publication.plan.head_sha,
            draft: true,
          },
        };
      else throw new Error(`Unexpected request ${path}`);
      return new Response(JSON.stringify(result), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("development control panel", () => {
  it("allows replacing an expired plan without approving it", async () => {
    run.publications = [
      { ...publication, plan: { ...publication.plan, expires_at: 1 } },
    ];
    render(<App />);
    const user = userEvent.setup();
    await screen.findByText("This change is ready for delivery");
    await user.click(screen.getByRole("tab", { name: "Publication" }));
    expect(
      (
        screen.getByRole("button", {
          name: "Approve & publish draft",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    await user.click(
      screen.getByRole("button", { name: "Prepare a new plan" }),
    );
    expect(screen.getByLabelText("GitHub repository")).toBeTruthy();
    expect(requests.filter((r) => r.init.method === "POST")).toHaveLength(0);
  });
  it("shows actual candidate evidence and renders repository text safely", async () => {
    render(<App />);
    const user = userEvent.setup();
    await screen.findByText("This change is ready for delivery");
    await user.click(screen.getByRole("tab", { name: "Checks & review" }));
    expect(await screen.findByText("15 tests passed")).toBeTruthy();
    expect(
      screen.getByText("The capped retry behavior is correct."),
    ).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: /Changes/ }));
    expect(
      await screen.findByText('+delay = "<img src=x onerror=alert(1)>"'),
    ).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    expect(
      requests.some((r) => r.path.includes(`candidate=${candidate}`)),
    ).toBe(true);
  });
  it("requires an explicit plan approval and sends the exact digest", async () => {
    render(<App />);
    const user = userEvent.setup();
    await screen.findByText("This change is ready for delivery");
    await user.click(screen.getByRole("tab", { name: "Publication" }));
    await user.type(
      screen.getByLabelText("GitHub repository"),
      "owner/retry-helper",
    );
    expect(requests.filter((r) => r.init.method === "POST")).toHaveLength(0);
    await user.click(
      screen.getByRole("button", { name: "Prepare publication plan" }),
    );
    await screen.findByText("Awaiting approval");
    expect(requests.filter((r) => r.path.endsWith("/publish"))).toHaveLength(0);
    await user.click(
      screen.getByRole("button", { name: "Approve & publish draft" }),
    );
    await screen.findByText("Draft pull request #1");
    const sent = requests.find((r) => r.path.endsWith("/publish"))!;
    expect(sent.path).toContain(planId);
    expect(JSON.parse(sent.init.body as string)).toEqual({
      plan_sha256: planId,
    });
  });
  it("does not offer writable controls to a viewer", async () => {
    role = "viewer";
    render(<App />);
    const user = userEvent.setup();
    await screen.findByText("This change is ready for delivery");
    await user.click(screen.getByRole("tab", { name: "Publication" }));
    expect(
      (
        screen.getByRole("button", {
          name: "Prepare publication plan",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      (screen.getByLabelText("GitHub repository") as HTMLInputElement).disabled,
    ).toBe(true);
    await waitFor(() =>
      expect(requests.filter((r) => r.init.method === "POST")).toHaveLength(0),
    );
  });
});
