import { Fragment } from 'react';

import type { Job, Lane, Stage } from '../types';

function StepList({ stages, lane }: { stages: Stage[]; lane: Lane }) {
  return (
    <div className={lane === 'merge' ? 'wf-merge' : 'steps'}>
      {stages.map((stage, i) => {
        const bits = [stage.detail, stage.elapsed ? `${stage.elapsed}s` : '']
          .filter(Boolean)
          .join(' · ');
        return (
          <Fragment key={stage.id}>
            <div className="step" data-status={stage.status}>
              <div className="step-top">
                <span className="step-dot" />
                <span className="step-label">{stage.label}</span>
              </div>
              <div className="step-detail" title={bits}>
                {bits}
              </div>
            </div>
            {i < stages.length - 1 && (
              <span className="step-arrow">{lane === 'merge' ? '↓' : '→'}</span>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

export function Workflow({ job }: { job: Job }) {
  const byLane = (lane: Lane) => Object.values(job.stages).filter((s) => s.lane === lane);

  return (
    <div className="panel">
      <h3>Workflow</h3>
      <div className="wf">
        <div className="wf-lanes">
          {(
            [
              ['a', job.label_a],
              ['b', job.label_b],
            ] as const
          ).map(([lane, label]) => (
            <div className="lane" data-lane={lane} key={lane}>
              <div className="lane-head">
                <span className="lane-tag">{lane.toUpperCase()}</span>
                <span className="lane-name" title={label}>
                  {label}
                </span>
              </div>
              <StepList stages={byLane(lane)} lane={lane} />
            </div>
          ))}
        </div>

        {/* The lanes really do run concurrently, so the join reflects
            execution rather than decorating it. */}
        <div className="wf-join">
          <svg width="46" height="130" viewBox="0 0 46 130" aria-hidden="true">
            <path d="M0 34 C 24 34, 22 65, 46 65" stroke="#3a4049" fill="none" strokeWidth="1.5" />
            <path d="M0 96 C 24 96, 22 65, 46 65" stroke="#3a4049" fill="none" strokeWidth="1.5" />
          </svg>
        </div>

        <StepList stages={byLane('merge')} lane="merge" />
      </div>
    </div>
  );
}
