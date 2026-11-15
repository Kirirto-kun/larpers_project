import { test, expect } from "@playwright/test";
import { fixtures } from "./fixtures";
test("SSE numeric cursor deduplicates reconnects and resets", async ({
  page,
}) => {
  const f = await fixtures(page);
  await page.addInitScript(() => {
    const streams: any[] = [];
    (window as any).__streams = streams;
    (window as any).EventSource = class {
      url: string;
      onmessage: any;
      onerror: any;
      onopen: any;
      listeners: Record<string, () => void> = {};
      constructor(url: string) {
        this.url = url;
        streams.push(this);
      }
      addEventListener(n: string, fn: () => void) {
        this.listeners[n] = fn;
      }
      close() {}
    };
  });
  await page.goto("/jobs/job-1");
  await expect(
    page.getByRole("heading", { name: "Task · job-1" }),
  ).toBeVisible();
  const emit = (kind: string) =>
    page.evaluate((kind) => {
      const s = (window as any).__streams.at(-1);
      if (kind === "message")
        s.onmessage({ data: JSON.stringify({ event_id: 42 }) });
      else if (kind === "reset") s.listeners.reset();
      else s.onerror();
    }, kind);
  await emit("message");
  await expect
    .poll(() => f.requests.filter((r) => r.path === "/jobs/job-1").length)
    .toBeGreaterThan(1);
  await emit("error");
  await expect
    .poll(() => page.evaluate(() => (window as any).__streams.at(-1).url))
    .toContain("cursor=42");
  await emit("reset");
  await emit("error");
  await expect
    .poll(() => page.evaluate(() => (window as any).__streams.at(-1).url))
    .not.toContain("cursor=");
});
