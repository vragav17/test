import { ago } from '../format';
import { goToJob, goToNew } from '../hooks/useHashRoute';
import type { JobListEntry, JobStatus } from '../types';

const STATUS_COLOR: Record<JobStatus, string> = {
  done: 'var(--ok)',
  failed: 'var(--err)',
  running: 'var(--accent)',
  queued: 'var(--muted)',
  cancelled: 'var(--muted)',
};

export function Sidebar({ jobs, activeId }: { jobs: JobListEntry[]; activeId: string | null }) {
  return (
    <aside className="sidebar">
      <button type="button" className="btn primary block" onClick={goToNew}>
        New comparison
      </button>
      <div className="sidebar-title">History</div>
      <div className="job-list">
        {jobs.length === 0 && <div className="empty-note">No comparisons yet.</div>}
        {jobs.map((job) => {
          const bits: string[] = [job.status];
          if (job.summary) bits.push(`${job.summary.region_count} region(s)`);
          bits.push(ago(job.created_at));
          return (
            <div
              key={job.id}
              className={`job-item${job.id === activeId ? ' active' : ''}`}
              onClick={() => goToJob(job.id)}
              onKeyDown={(e) => e.key === 'Enter' && goToJob(job.id)}
              role="button"
              tabIndex={0}
            >
              <div className="names">
                {job.label_a} ↔ {job.label_b}
              </div>
              <div className="meta">
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    flex: 'none',
                    background: STATUS_COLOR[job.status],
                  }}
                />
                <span>{bits.join(' · ')}</span>
              </div>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
