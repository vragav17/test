import { useEffect, useState } from 'react';

import { browse } from '../api';
import { fmtBytes } from '../format';
import type { BrowseResult } from '../types';

interface Props {
  startPath: string;
  onPick: (path: string) => void;
  onClose: () => void;
}

export function FilePicker({ startPath, onPick, onClose }: Props) {
  const [path, setPath] = useState(startPath);
  const [data, setData] = useState<BrowseResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setError(null);
    browse(path)
      .then((d) => alive && setData(d))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [path]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      onClick={(e) => e.target === e.currentTarget && onClose()}
      role="presentation"
    >
      <div className="modal">
        <div className="modal-head">
          <div>
            <div className="modal-title">Choose a video</div>
            <div className="modal-path">{data?.path ?? path}</div>
          </div>
          <button type="button" className="btn ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="modal-body">
          {error && <div className="banner err">{error}</div>}
          {data?.parent && (
            <div className="fs-row" onClick={() => setPath(data.parent!)} role="button" tabIndex={0}>
              <span className="fs-icon">↰</span>
              <span>.. (up one level)</span>
            </div>
          )}
          {data?.dirs.map((d) => (
            <div key={d.path} className="fs-row" onClick={() => setPath(d.path)} role="button" tabIndex={0}>
              <span className="fs-icon">▸</span>
              <span>{d.name}</span>
            </div>
          ))}
          {data?.videos.map((v) => (
            <div key={v.path} className="fs-row" onClick={() => onPick(v.path)} role="button" tabIndex={0}>
              <span className="fs-icon">▦</span>
              <span>{v.name}</span>
              <span className="fs-size">{fmtBytes(v.size)}</span>
            </div>
          ))}
          {data && !data.dirs.length && !data.videos.length && (
            <div className="empty-note">Nothing here.</div>
          )}
        </div>
      </div>
    </div>
  );
}
