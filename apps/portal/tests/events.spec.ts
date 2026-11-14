import { test, expect } from "@playwright/test";
import { fixtures } from "./fixtures";
test("SSE resumes with cursor, ignores immediate duplicates, and resets an expired cursor", async ({
  page,
}) => {
  const fixture = await fixtures(page);
  await page.addInitScript(() => {
    const streams: any[] = [];
    (window as any).__streams = streams;
    (window as any).EventSource = class {
      url: string;
      closed = false;
      onmessage: any;
      onerror: any;
      onopen: any;
      listeners: Record<string, () => void> = {};
      constructor(url: string) {
        this.url = url;
        streams.push(this);
      }
      addEventListener(name: string, fn: () => void) {
        this.listeners[name] = fn;
      }
      close() {
        this.closed = true;
      }
    };
  });
  await page.goto("jobs/job-1");
  await expect(
    page.getByRole("heading", { name: "Document corpus" }),
  ).toBeVisible();
  const emit = async (type: string, data?: unknown) =>
    page.evaluate(
      ({ type, data }) => {
        const stream = (window as any).__streams.at(-1);
        if (type === "message")
          stream.onmessage({ data: JSON.stringify(data) });
        else if (type === "reset") stream.listeners.reset();
        else stream.onerror();
      },
      { type, data },
    );
  const event = {
    event_id: "event-1",
    entity_type: "job",
    entity_id: "job-1",
    type: "changed",
    occurred_at: "2026-11-14T00:00:00Z",
    payload: {},
  };
  await emit("message", event);
  await expect
    .poll(() => fixture.requests.filter((r) => r.path === "/jobs/job-1").length)
    .toBeGreaterThan(1);
  const count = fixture.requests.filter((r) => r.path === "/jobs/job-1").length;
  await emit("message", event);
  expect(fixture.requests.filter((r) => r.path === "/jobs/job-1").length).toBe(
    count,
  );
  await emit("error");
  await expect
    .poll(() => page.evaluate(() => (window as any).__streams.at(-1).url))
    .toContain("cursor=event-1");
  await emit("reset");
  await emit("error");
  await expect
    .poll(() => page.evaluate(() => (window as any).__streams.at(-1).url))
    .not.toContain("cursor=");
});
