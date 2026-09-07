import { useState } from 'react';

import { TYPE_BLURB, TYPE_COLOR, TYPE_LABEL, fmtTc } from '../format';
import type { Region, RegionThumbs, RegionType } from '../types';
import { RegionCard } from './RegionCard';

export interface GroupedRegion {
  region: Region;
  index: number;
  thumbs: RegionThumbs | undefined;
}

/**
 * One collapsible group per kind of change.
 *
 * A flat list of thirty regions is a wall. Grouping answers the question people
 * actually arrive with — "what *kinds* of things changed, and how many?" —
 * before asking them to read any individual timecode.
 */
export function ChangeGroup({
  kind, items, jobId, flash,
}: {
  kind: RegionType;
  items: GroupedRegion[];
  jobId?: string;
  flash: { index: number; key: number } | null;
}) {
  const [open, setOpen] = useState(true);
  const colour = TYPE_COLOR[kind];

  // Total footprint of this kind of change, on whichever side carries it.
  const seconds = items.reduce((total, { region }) => {
    const a = region.a_end - region.a_start;
    const b = region.b_end - region.b_start;
    return total + Math.max(a, b);
  }, 0);
  const shots = items.reduce((total, { region }) => total + region.shot_count, 0);

  return (
    <section className="group" style={{ borderLeftColor: colour }}>
      <header
        className="group-head"
        onClick={() => setOpen((v) => !v)}
        role="button"
        tabIndex={0}
      >
        <span className={`chev${open ? ' open' : ''}`}>▶</span>
        <span className="swatch" style={{ background: colour }} />
        <h3 className="group-title">{TYPE_LABEL[kind]}</h3>
        <span className="group-count">{items.length}</span>
        <span className="group-meta">
          {shots} shot{shots === 1 ? '' : 's'} · {fmtTc(seconds)} total
        </span>
      </header>
      {open && (
        <>
          <p className="group-blurb">{TYPE_BLURB[kind]}</p>
          <div className="group-body">
            {items.map(({ region, index, thumbs }) => (
              <RegionCard
                key={index}
                index={index}
                region={region}
                thumbs={thumbs}
                jobId={jobId}
                showType={false}
                flashed={flash?.index === index}
                flashKey={flash?.key ?? 0}
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
