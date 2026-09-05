import type { Health } from '../types';

function Badge({ label, state, title }: { label: string; state: string; title?: string }) {
  return (
    <div className={`badge ${state}`} title={title}>
      <span className="dot" />
      <span>{label}</span>
    </div>
  );
}

export function TopBar({ health }: { health: Health | null }) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" />
        <div>
          <div className="brand-name">Version Diff</div>
          <div className="brand-sub">Local shot-level comparison of two cuts</div>
        </div>
      </div>
      <div className="env">
        {health && (
          <>
            <Badge
              label={health.ffmpeg ? 'ffmpeg' : 'ffmpeg missing'}
              state={health.ffmpeg ? 'on' : 'off'}
              title={health.ffmpeg_error}
            />
            <Badge
              label={health.videotoolbox ? 'videotoolbox' : 'software encode'}
              state={health.videotoolbox ? 'on' : 'warn'}
              title={
                health.videotoolbox
                  ? 'Hardware encode available'
                  : 'h264_videotoolbox unavailable; using libx264'
              }
            />
            <Badge
              label={health.ollama ? `ollama (${health.models.length} models)` : 'ollama offline'}
              state={health.ollama ? 'on' : 'warn'}
              title={
                health.ollama
                  ? health.models.join(', ')
                  : 'Descriptions unavailable until `ollama serve` is running'
              }
            />
          </>
        )}
      </div>
    </header>
  );
}
