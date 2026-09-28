'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, subscribe } from './api';
import type { RunEvent, RunSummary, RunView } from './types';

/** Danh sách run, làm mới định kỳ. */
export function useRuns(intervalMs = 3000) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      setRuns(await api.runs());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), intervalMs);
    return () => clearInterval(t);
  }, [refresh, intervalMs]);
  return { runs, error, refresh };
}

/** Một run: RunReport + chuỗi sự kiện SSE. View được nạp lại mỗi khi có sự kiện mới. */
export function useRun(id: string | null) {
  const [view, setView] = useState<RunView | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);

  const reload = useCallback(async () => {
    if (!id) return;
    try {
      setView(await api.run(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [id]);

  useEffect(() => {
    setView(null);
    setEvents([]);
    if (!id) return undefined;
    let closed = false;
    let unsubscribe: (() => void) | null = null;
    void (async () => {
      await reload();
      const initial = await api.events(id).catch(() => [] as RunEvent[]);
      if (closed) return;
      setEvents(initial);
      const after = initial.length ? initial[initial.length - 1].seq : 0;
      unsubscribe = subscribe(id, after, (e) => {
        setEvents((prev) => (prev.some((p) => p.seq === e.seq) ? prev : [...prev, e]));
        if (pending.current) clearTimeout(pending.current);
        pending.current = setTimeout(() => void reload(), 250);
      });
    })();
    return () => {
      closed = true;
      unsubscribe?.();
      if (pending.current) clearTimeout(pending.current);
    };
  }, [id, reload]);

  return { view, events, error, reload };
}
