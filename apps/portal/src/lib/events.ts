import type { PoolEvent } from "./types";
// Re-fetch authoritative snapshots on every event, reset, or connection interruption.
export function subscribe(
  path: string,
  refresh: () => void,
  onConnection: (connected: boolean) => void,
) {
  let closed = false,
    source: EventSource | undefined,
    timer: ReturnType<typeof setTimeout>,
    last = "";
  const connect = () => {
    if (closed) return;
    source = new EventSource(
      "/api/v1" + path + (last ? "?cursor=" + encodeURIComponent(last) : ""),
      { withCredentials: true },
    );
    source.onopen = () => {
      onConnection(true);
      refresh();
    };
    source.onmessage = (message) => {
      try {
        const event: PoolEvent = JSON.parse(message.data);
        if (String(event.event_id) === last) return;
        last = String(event.event_id);
        refresh();
      } catch {
        refresh();
      }
    };
    source.addEventListener("reset", () => {
      last = "";
      refresh();
    });
    source.onerror = () => {
      onConnection(false);
      refresh();
      source?.close();
      timer = setTimeout(connect, 3000);
    };
  };
  connect();
  return () => {
    closed = true;
    source?.close();
    clearTimeout(timer);
  };
}
