import { useEffect, useRef } from 'react';

import type { LogLine } from '../types';

export function PipelineLog({ logs }: { logs: LogLine[] }) {
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // Follow the tail, but stop following the moment the user scrolls up.
  useEffect(() => {
    const node = box.current;
    if (node && pinned.current) node.scrollTop = node.scrollHeight;
  }, [logs]);

  const onScroll = () => {
    const node = box.current;
    if (!node) return;
    pinned.current = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
  };

  return (
    <div className="panel">
      <h3>Pipeline output</h3>
      <div className="log" ref={box} onScroll={onScroll}>
        {logs.length === 0 && <div className="log-line">Waiting for output…</div>}
        {logs.map((entry, i) => (
          // Log lines are an append-only stream with no stable id of their own.
          // eslint-disable-next-line react/no-array-index-key
          <div key={i} className={`log-line ${entry.lane}${entry.warn ? ' warn' : ''}`}>
            <span className="tag">{entry.lane === 'merge' ? '' : entry.lane.toUpperCase()}</span>
            <span>{entry.line}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
