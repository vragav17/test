import { useEffect, useRef, useState } from 'react';

/**
 * One compact play control, sized to sit inside a version column.
 *
 * Only one clip plays at a time across the whole page. That is the point of
 * the control: you are A/B-ing two versions of the same moment, so starting B
 * should stop A rather than talk over it.
 */
let nowPlaying: HTMLAudioElement | null = null;

function fmt(seconds: number) {
  if (!Number.isFinite(seconds)) return '--:--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function AudioButton({ src, side }: { src: string | null; side: 'a' | 'b' }) {
  const ref = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(NaN);
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');

  useEffect(() => () => {
    // Leaving the page mid-play should not leave audio running.
    if (ref.current && nowPlaying === ref.current) nowPlaying = null;
    ref.current?.pause();
  }, []);

  if (!src) {
    return <div className="audio-btn-row none">No audio on this side.</div>;
  }

  const toggle = async () => {
    const el = ref.current;
    if (!el) return;
    if (playing) {
      el.pause();
      return;
    }
    if (nowPlaying && nowPlaying !== el) nowPlaying.pause();
    nowPlaying = el;
    if (state === 'idle') setState('loading');
    try {
      await el.play();
    } catch {
      setState('error');
    }
  };

  const progress = Number.isFinite(duration) && duration > 0 ? (time / duration) * 100 : 0;

  return (
    <div className={`audio-btn-row ${side}`}>
      <button
        type="button"
        className={`audio-btn${playing ? ' playing' : ''}`}
        onClick={toggle}
        disabled={state === 'error'}
        aria-label={playing ? `Pause version ${side.toUpperCase()}` : `Play version ${side.toUpperCase()}`}
        title={state === 'error' ? 'This clip could not be loaded'
          : `${playing ? 'Pause' : 'Play'} version ${side.toUpperCase()}`}
      >
        {state === 'loading' && !playing ? (
          <span className="audio-spinner" />
        ) : playing ? (
          <svg width="11" height="12" viewBox="0 0 11 12" aria-hidden="true">
            <rect x="0" y="0" width="3.5" height="12" rx="1" fill="currentColor" />
            <rect x="7" y="0" width="3.5" height="12" rx="1" fill="currentColor" />
          </svg>
        ) : (
          <svg width="11" height="12" viewBox="0 0 11 12" aria-hidden="true">
            <path d="M1 0.8 L10 6 L1 11.2 Z" fill="currentColor" />
          </svg>
        )}
      </button>

      <div className="audio-meter">
        <div className="audio-progress">
          <span style={{ width: `${progress}%` }} />
        </div>
        <div className="audio-time">
          {state === 'error'
            ? 'unavailable'
            : `${fmt(time)} / ${Number.isFinite(duration) ? fmt(duration) : '--:--'}`}
        </div>
      </div>

      <audio
        ref={ref}
        src={src}
        preload="none"
        onLoadedMetadata={(e) => {
          setDuration(e.currentTarget.duration);
          setState('ready');
        }}
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false);
          setTime(0);
        }}
        onError={() => setState('error')}
      />
    </div>
  );
}
