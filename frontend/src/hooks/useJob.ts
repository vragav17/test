import { useCallback, useEffect, useState } from 'react';

import { getJob, getLogHistory, getReport, getThumbs } from '../api';
import type { Job, JobEvent, Lane, LogLine, RegionThumbs, Report } from '../types';

export interface JobView {
  job: Job | null;
  logs: LogLine[];
  report: Report | null;
  thumbs: RegionThumbs[] | null;
  error: string | null;
  reload: () => void;
}

/**
 * Loads one job and keeps it live.
 *
 * While the job is queued or running it subscribes to the SSE stream and folds
 * stage transitions into local state so the workflow updates without polling.
 * Once the job reaches a terminal state the streamed log is replaced with the
 * server's authoritative history -- that also covers opening a job that
 * finished before this page was ever loaded.
 */
export function useJob(jobId: string | null): JobView {
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [thumbs, setThumbs] = useState<RegionThumbs[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    setLogs([]);
    setReport(null);
    setThumbs(null);
  }, [jobId]);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let alive = true;
    setError(null);
    getJob(jobId)
      .then((j) => alive && setJob(j))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [jobId, nonce]);

  const status = job?.status ?? null;

  useEffect(() => {
    if (!jobId || !status) return;

    if (status === 'queued' || status === 'running') {
      const es = new EventSource(`/api/jobs/${jobId}/events?since=0`);
      es.onmessage = (msg) => {
        const ev = JSON.parse(msg.data) as JobEvent;
        if (ev.type === 'stage') {
          setJob((prev) => {
            if (!prev) return prev;
            const existing = prev.stages[ev.stage];
            if (!existing) return prev;
            return {
              ...prev,
              stages: {
                ...prev.stages,
                [ev.stage]: {
                  ...existing,
                  status: ev.status,
                  detail: ev.detail || existing.detail,
                  elapsed: ev.elapsed,
                },
              },
            };
          });
        } else if (ev.type === 'log') {
          setLogs((prev) => [...prev, { lane: ev.lane, line: ev.line }]);
        } else if (ev.type === 'error') {
          setLogs((prev) => [...prev, { lane: 'merge', line: ev.message, warn: true }]);
        } else if (ev.type === 'finished' || ev.type === 'closed') {
          es.close();
          reload();
        }
      };
      es.onerror = () => es.close();
      return () => es.close();
    }

    let alive = true;
    getLogHistory(jobId)
      .then((hist) => {
        if (!alive) return;
        setLogs(
          hist.map((e) =>
            e.type === 'error'
              ? { lane: 'merge' as Lane, line: e.message ?? '', warn: true }
              : { lane: (e.lane ?? 'merge') as Lane, line: e.line ?? '' },
          ),
        );
      })
      .catch(() => {
        /* history is a nicety; a missing log must not break the view */
      });
    return () => {
      alive = false;
    };
  }, [jobId, status, reload]);

  useEffect(() => {
    if (!jobId || status !== 'done') return;
    let alive = true;
    Promise.all([getReport(jobId), getThumbs(jobId)])
      .then(([r, t]) => {
        if (!alive) return;
        setReport(r);
        setThumbs(t);
      })
      .catch(() => {
        /* the report may still be flushing to disk; the next reload picks it up */
      });
    return () => {
      alive = false;
    };
  }, [jobId, status]);

  return { job, logs, report, thumbs, error, reload };
}
