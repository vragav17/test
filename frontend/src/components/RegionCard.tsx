import { useEffect, useState } from 'react';

import { TYPE_BLURB, TYPE_COLOR, TYPE_LABEL, fmtTc } from '../format';
import type { Region, RegionThumbs } from '../types';

interface Props {
  index: number;
  region: Region;
  thumbs: RegionThumbs | undefined;
  flashKey: number;
  flashed: boolean;
}

export function RegionCard({ index, region, thumbs, flashKey, flashed }: Props) {
  // Open by default: the thumbnails are the substance when there are no
  // descriptions, so they should be on screen without a click.
  const [open, setOpen] = useState(true);
  const [flash, setFlash] = useState(false);

  useEffect(() => {
    if (!flashed) return;
    setOpen(true);
    setFlash(true);
    const timer = window.setTimeout(() => setFlash(false), 900);
    return () => window.clearTimeout(timer);
  }, [flashed, flashKey]);

  const colour = TYPE_COLOR[region.type] ?? '#888';

  return (
    <div
      id={`r${index}`}
      className={`region-card${open ? ' open' : ''}${flash ? ' flash' : ''}`}
      style={{ borderLeftColor: colour }}
    >
      <div className="rc-head" onClick={() => setOpen((v) => !v)} role="button" tabIndex={0}>
        <span className="chev">▶</span>
        <span className="swatch" style={{ background: colour }} />
        <span className="rc-type">{TYPE_LABEL[region.type] ?? region.type}</span>
        <span className="none">{region.shot_count} shot(s)</span>
        <div className="rc-times">
          A {fmtTc(region.a_start)} → {fmtTc(region.a_end)}
          <br />
          B {fmtTc(region.b_start)} → {fmtTc(region.b_end)}
        </div>
      </div>

      <div className="rc-body">
        <div className="muted">{TYPE_BLURB[region.type]}</div>
        <div className={`explanation${region.explanation ? '' : ' absent'}`}>
          {region.explanation ??
            'No description (re-run with descriptions enabled to add one).'}
        </div>
        <div className="sides">
          {(
            [
              ['a', 'Version A'],
              ['b', 'Version B'],
            ] as const
          ).map(([side, name]) => {
            const start = side === 'a' ? region.a_start : region.b_start;
            const end = side === 'a' ? region.a_end : region.b_end;
            const images = (side === 'a' ? thumbs?.thumbnails_a : thumbs?.thumbnails_b) ?? [];
            const description = side === 'a' ? region.description_a : region.description_b;
            return (
              <div className="side" key={side}>
                <div className="side-title">{name}</div>
                <div className="side-time">
                  {end - start <= 0.01 ? (
                    <>at {fmtTc(start)} — nothing here</>
                  ) : (
                    <>
                      {fmtTc(start)} → {fmtTc(end)}{' '}
                      <span className="none">({(end - start).toFixed(2)}s)</span>
                    </>
                  )}
                </div>
                <div className="shots">
                  {images.length === 0 && <div className="none">No frames on this side.</div>}
                  {images.map((img, i) => (
                    <img
                      // eslint-disable-next-line react/no-array-index-key
                      key={i}
                      src={`data:image/jpeg;base64,${img}`}
                      alt={`${name} frame ${i + 1}`}
                    />
                  ))}
                </div>
                {description && (
                  <div className="muted" style={{ fontSize: '12.5px', marginTop: 9 }}>
                    {description}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
