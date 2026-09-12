import { afterEach, expect, it, vi } from "vitest";
import { api, connect, externalUrl } from "./api";

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
});
it("removes the launch token from the URL and authenticates through headers", async () => {
  history.replaceState(null, "", "/#token=local-test-session");
  connect();
  const fetch = vi.fn(
    async (_path: string, _init: RequestInit) =>
      new Response("{}", { status: 200 }),
  );
  vi.stubGlobal("fetch", fetch);
  await api("/session");
  expect(location.hash).toBe("");
  expect(fetch.mock.calls[0][0]).toBe("/api/session");
  expect(
    (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].headers,
  ).toEqual({ Authorization: "Bearer local-test-session" });
});
it("rejects executable and insecure external links", () => {
  expect(externalUrl("javascript:alert(1)")).toBeUndefined();
  expect(externalUrl("http://example.invalid")).toBeUndefined();
  expect(externalUrl("https://github.com/owner/repo/pull/1")).toContain(
    "github.com",
  );
});
