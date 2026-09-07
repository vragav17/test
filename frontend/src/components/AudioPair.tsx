import { useState } from 'react';

/**
 * Side-by-side players for the audio under a region.
 *
 * The whole point of an `audio_changed` finding is that the pictures are
 * identical — the thumbnails prove nothing on their own. Hearing both sides is
 * how a person actually confirms it.
 *
 * `preload="none"` matters: the clip is cut server-side on first request, so
 * loading every player on render would fire an ffmpeg process per region for
 * audio nobody asked to hear.
 */
export function AudioPair({
  jobId, regionIndex, hasA, hasB, inlineA, inlineB,
}: {
  jobId?: string;
  regionIndex: number;
  hasA: boolean;
  hasB: boolean;
  inlineA?: string | null;
  inlineB?: string | null;
}) {
  const [failed, setFailed] = useState<Set<string>>(new Set());

  const sourceFor = (side: 'a' | 'b') => {
    const inline = side === 'a' ? inlineA : inlineB;
    if (inline) return `data:audio/mpeg;base64,${inline}`;
    if (jobId) return `/api/jobs/${jobId}/audio/${regionIndex}/${side}`;
    return null;
  };

  const sides: Array<['a' | 'b', string, boolean]> = [
    ['a', 'A', hasA],
    ['b', 'B', hasB],
  ];
  const playable = sides.filter(([side, , has]) => has && sourceFor(side));
  if (!playable.length) return null;

  return (
    <div className="audio-pair">
      <div className="audio-label">Listen</div>
      <div className="audio-rows">
        {playable.map(([side, tag]) => (
          <div className="audio-row" key={side}>
            <span className={`audio-tag ${side}`}>{tag}</span>
            {failed.has(side) ? (
              <span className="none">No audio track on this side.</span>
            ) : (
              <audio
                controls
                preload="none"
                src={sourceFor(side) ?? undefined}
                onError={() => setFailed((prev) => new Set(prev).add(side))}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
