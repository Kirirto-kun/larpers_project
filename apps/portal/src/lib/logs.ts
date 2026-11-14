import { useEffect, useState } from "react";
import { api, errorText } from "./api";
interface LogEntry {
  cursor: number;
  text: string;
  gap: boolean;
  occurred_at: string;
}
interface LogPage {
  entries: LogEntry[];
  cursor: number;
  truncated: boolean;
}
export function useJobLogs(id: string | undefined) {
  const [data, setData] = useState<LogPage>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true,
      cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    setData(undefined);
    setError("");
    async function read() {
      let delay = 5000;
      try {
        const next = await api<LogPage>(
          "/jobs/" + id + "/logs?cursor=" + cursor,
        );
        if (!active) return;
        const previousCursor = cursor;
        cursor = Math.max(cursor, next.cursor);
        setData((previous) => {
          const entries = [
            ...(previous?.entries || []),
            ...next.entries.filter((e) => e.cursor > previousCursor),
          ];
          return {
            cursor,
            entries: entries.slice(-2000),
            truncated:
              !!previous?.truncated || next.truncated || entries.length > 2000,
          };
        });
        setError("");
        if (next.entries.length === 100 && cursor > previousCursor) delay = 0;
      } catch (e) {
        if (active) setError(errorText(e));
      }
      if (active) timer = setTimeout(read, delay);
    }
    void read();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id]);
  return { data, error };
}
