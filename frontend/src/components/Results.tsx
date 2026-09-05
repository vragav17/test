import { useState } from 'react';

import { REGION_TYPES, TYPE_COLOR, TYPE_LABEL, fmtTc, shortTc } from '../format';
import type { Region, RegionThumbs, RegionType, Report, VersionInfo } from '../types';
import { RegionCard } from './RegionCard';

function Track({
  tag, info, regions, side, maxDuration, onPick,
}: {
  tag: 'A' | 'B';
  info: VersionInfo;
  regions: Region[];
  side: 'a' | 'b';
  maxDuration: number;
  onPick: (index: number) => void;
}) {
  const duration = info.duration_seconds || 1;
  return (
    <div className="track">
      <div className="track-head">
        <div>
          <span
            className="lane-tag"
            style={{
              display: 'inline-grid', placeItems: 'center', width: 18, height: 18,
              marginRight: 8, borderRadius: 5, background: 'var(--panel-2)',
              fontSize: '10.5px', fontWeight: 700,
              color: tag === 'A' ? 'var(--accent)' : 'var(--audio)',
            }}
          >
            {tag}
          </span>
          <b>{info.source}</b>
        </div>
        <div className="dur">
          {fmtTc(duration)} · {info.shot_count} shots
        </div>
      </div>
      <div className="bar" style={{ width: `${(100 * duration) / maxDuration}%` }}>
        {regions.map((region, i) => {
          const start = side === 'a' ? region.a_start : region.b_start;
          const end = side === 'a' ? region.a_end : region.b_end;
          return (
            <div
              key={`${region.type}-${i}`}
              className={`region${end - start <= 0.01 ? ' point' : ''}`}
              style={{
                left: `${(100 * start) / duration}%`,
                width: `${(100 * Math.max(end - start, 0)) / duration}%`,
                background: TYPE_COLOR[region.type] ?? '#888',
              }}
              title={`${TYPE_LABEL[region.type]} — ${fmtTc(start)} to ${fmtTc(end)} (${region.shot_count} shot(s))`}
              onClick={() => onPick(i)}
              role="button"
              tabIndex={0}
            />
          );
        })}
      </div>
    </div>
  );
}

export function Results({ report, thumbs }: { report: Report; thumbs: RegionThumbs[] | null }) {
  const [filters, setFilters] = useState<Set<RegionType>>(new Set());
  const [flash, setFlash] = useState<{ index: number; key: number } | null>(null);

  const { regions } = report;
  const maxDuration = Math.max(
    report.version_a.duration_seconds,
    report.version_b.duration_seconds,
    1,
  );
  const delta = report.version_b.duration_seconds - report.version_a.duration_seconds;
  const present = REGION_TYPES.filter((k) => report.summary[k]);

  const pick = (index: number) => {
    setFlash({ index, key: Date.now() });
    document.getElementById(`r${index}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const toggle = (kind: RegionType) => {
    setFilters((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  return (
    <>
      <div className="stats">
        <div className="stat">
          <div className="n">{report.region_count}</div>
          <div className="k">changed regions</div>
        </div>
        {present.map((kind) => (
          <div className={`stat ${kind}`} key={kind}>
            <div className="n">{report.summary[kind]}</div>
            <div className="k">{TYPE_LABEL[kind]}</div>
          </div>
        ))}
        <div className="stat">
          <div className="n">
            {delta >= 0 ? '+' : ''}
            {delta.toFixed(1)}s
          </div>
          <div className="k">runtime difference</div>
        </div>
      </div>

      <div className="panel">
        <h3>Timelines (shared scale)</h3>
        <Track tag="A" info={report.version_a} regions={regions} side="a" maxDuration={maxDuration} onPick={pick} />
        <Track tag="B" info={report.version_b} regions={regions} side="b" maxDuration={maxDuration} onPick={pick} />
        <div className="ruler">
          {Array.from({ length: 9 }, (_, i) => (
            <div key={i} className="tick" style={{ left: `${(i / 8) * 100}%` }}>
              {shortTc((maxDuration * i) / 8)}
            </div>
          ))}
        </div>
        <div className="legend">
          {present.map((kind) => (
            <span key={kind}>
              <i className="swatch" style={{ background: TYPE_COLOR[kind] }} />
              <span>
                {TYPE_LABEL[kind]} ({report.summary[kind]})
              </span>
            </span>
          ))}
          {present.length === 0 && <span>No changes detected.</span>}
        </div>
      </div>

      {regions.length === 0 ? (
        <div className="banner ok">
          No differences found. The two versions align shot for shot, with matching picture and
          audio throughout.
        </div>
      ) : (
        <>
          {present.length > 1 && (
            <div className="filters">
              {present.map((kind) => (
                <div
                  key={kind}
                  className={`chip${filters.size === 0 || filters.has(kind) ? ' on' : ''}`}
                  onClick={() => toggle(kind)}
                  role="button"
                  tabIndex={0}
                >
                  <i className="swatch" style={{ background: TYPE_COLOR[kind], marginRight: 7 }} />
                  <span>{TYPE_LABEL[kind]}</span>
                </div>
              ))}
            </div>
          )}
          {regions.map((region, i) =>
            filters.size && !filters.has(region.type) ? null : (
              <RegionCard
                key={i}
                index={i}
                region={region}
                thumbs={thumbs?.[i]}
                flashed={flash?.index === i}
                flashKey={flash?.key ?? 0}
              />
            ),
          )}
        </>
      )}
    </>
  );
}
