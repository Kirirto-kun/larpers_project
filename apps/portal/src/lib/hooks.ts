import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "./api";
import { subscribe } from "./events";
export function useResource<T>(path: string, events?: string) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const request = ++sequence.current;
    try {
      const next = await api<T>(path);
      if (request === sequence.current) {
        setData(next);
        setError("");
      }
    } catch (e) {
      if (request === sequence.current) setError(errorText(e));
    }
  }, [path]);
  useEffect(() => {
    setData(undefined);
    setError("");
    setConnected(false);
    void refresh();
    const stop = events
      ? subscribe(events, () => void refresh(), setConnected)
      : () => {};
    return () => {
      ++sequence.current;
      stop();
    };
  }, [refresh, events]);
  return { data, error, refresh, connected };
}
