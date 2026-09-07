import type { TechnicalDiff, VersionInfo } from '../types';

/**
 * Delivery properties that differ — HDR vs SDR, bit depth, frame rate.
 *
 * Deliberately separate from the timeline: nothing here means the *edit*
 * changed, and none of it has a timecode. Two files can align shot for shot
 * and still not be the same delivery.
 */
export function TechnicalDiffs({
  differences, a, b,
}: {
  differences: TechnicalDiff[];
  a: VersionInfo;
  b: VersionInfo;
}) {
  if (!differences.length) return null;

  const material = differences.filter((d) => d.material).length;
  const aRange = a.technical?.dynamic_range;
  const bRange = b.technical?.dynamic_range;
  const headline = aRange && bRange && aRange !== bRange
    ? `${aRange}  vs  ${bRange}`
    : null;

  return (
    <div className="panel">
      <h3>Technical differences</h3>
      {headline && <div className="tech-headline">{headline}</div>}
      <table className="tech-table">
        <thead>
          <tr><th>Property</th><th>A</th><th>B</th></tr>
        </thead>
        <tbody>
          {differences.map((d) => (
            <tr key={d.field} className={d.material ? 'material' : ''}>
              <td>
                {d.material && <span className="material-dot" title="material difference" />}
                {d.label}
              </td>
              <td>{d.a ?? '—'}</td>
              <td>{d.b ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="tech-note">
        {differences.length} difference(s), {material} material. These are delivery
        properties, not edit changes — they have no timecode and do not appear on the
        timelines below.
      </div>
    </div>
  );
}
