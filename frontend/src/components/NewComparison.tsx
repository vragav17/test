import { useState } from 'react';

import { createJob } from '../api';
import { goToJob } from '../hooks/useHashRoute';
import type { Health } from '../types';
import { FilePicker } from './FilePicker';

export function NewComparison({ health }: { health: Health | null }) {
  const [videoA, setVideoA] = useState('');
  const [videoB, setVideoB] = useState('');
  const [threshold, setThreshold] = useState('27');
  const [audioThreshold, setAudioThreshold] = useState('16');
  const [explain, setExplain] = useState(false);
  const [model, setModel] = useState('qwen3-vl:8b');
  const [picking, setPicking] = useState<'a' | 'b' | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setError('');
    if (!videoA.trim() || !videoB.trim()) {
      setError('Pick both versions first.');
      return;
    }
    setBusy(true);
    try {
      const { id } = await createJob({
        video_a: videoA.trim(),
        video_b: videoB.trim(),
        threshold: parseFloat(threshold) || 27,
        audio_threshold: parseInt(audioThreshold, 10) || 16,
        explain,
        model: model.trim() || 'qwen3-vl:8b',
      });
      goToJob(id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const pickInto = (path: string) => {
    if (picking === 'a') setVideoA(path);
    else if (picking === 'b') setVideoB(path);
    setPicking(null);
  };

  const startPath = (value: string) => value.replace(/\/[^/]*$/, '');

  return (
    <div className="panel">
      <h2>New comparison</h2>
      <p className="muted">
        Pick two versions of the same title. Everything runs on this machine; nothing is
        uploaded anywhere.
      </p>

      <div className="pair">
        {(
          [
            ['a', 'Version A', '(the reference)', videoA, setVideoA],
            ['b', 'Version B', '(compared against A)', videoB, setVideoB],
          ] as const
        ).map(([side, title, note, value, setValue]) => (
          <div className="pick" data-side={side} key={side}>
            <div className="pick-tag">{side.toUpperCase()}</div>
            <label htmlFor={`video_${side}`}>
              {title} <span className="muted">{note}</span>
            </label>
            <div className="row">
              <input
                id={`video_${side}`}
                className="input"
                type="text"
                value={value}
                placeholder={`/path/to/version_${side}.mkv`}
                onChange={(e) => setValue(e.target.value)}
              />
              <button type="button" className="btn" onClick={() => setPicking(side)}>
                Browse
              </button>
            </div>
          </div>
        ))}
      </div>

      <details className="options">
        <summary>Options</summary>
        <div className="opt-grid">
          <label>
            Cut threshold
            <input
              className="input"
              type="number"
              step="0.5"
              min="1"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
            />
            <span className="hint">Lower finds more shots. Raise it for grainy film.</span>
          </label>
          <label>
            Audio change distance
            <input
              className="input"
              type="number"
              min="1"
              max="64"
              value={audioThreshold}
              onChange={(e) => setAudioThreshold(e.target.value)}
            />
            <span className="hint">
              Hamming distance above which matched shots count as audio-changed.
            </span>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={explain}
              onChange={(e) => setExplain(e.target.checked)}
            />
            Describe changes with a local model
            <span className="hint">
              Needs Ollama running. Off by default — the report works without it.
            </span>
          </label>
          <label>
            Model
            <input
              className="input"
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
            <span className="hint">
              {health?.ollama && health.models.length
                ? `Installed: ${health.models.join(', ')}`
                : 'Ollama is not reachable right now.'}
            </span>
          </label>
        </div>
      </details>

      <div className="actions">
        <button type="button" className="btn primary" onClick={run} disabled={busy}>
          {busy ? 'Starting…' : 'Run comparison'}
        </button>
        {error && <span className="muted">{error}</span>}
      </div>

      {picking && (
        <FilePicker
          startPath={startPath(picking === 'a' ? videoA : videoB)}
          onPick={pickInto}
          onClose={() => setPicking(null)}
        />
      )}
    </div>
  );
}
