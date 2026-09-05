import { cancelJob, downloadUrl, reportUrl } from '../api';
import { useJob } from '../hooks/useJob';
import { goToNew } from '../hooks/useHashRoute';
import { PipelineLog } from './PipelineLog';
import { Results } from './Results';
import { Workflow } from './Workflow';

export function JobView({ jobId }: { jobId: string }) {
  const { job, logs, report, thumbs, error } = useJob(jobId);

  if (error) return <div className="banner err">{error}</div>;
  if (!job) return <div className="panel muted">Loading…</div>;

  const running = job.status === 'queued' || job.status === 'running';

  return (
    <>
      <div className="head-row">
        <div>
          <h2>
            {job.label_a} ↔ {job.label_b}
          </h2>
          <div className="muted" style={{ fontSize: '12.5px' }}>
            {job.status} · threshold {job.threshold} · audio distance {job.audio_threshold}
            {job.explain ? ` · ${job.model}` : ''}
          </div>
        </div>
        <div className="head-actions">
          {running && (
            <button
              type="button"
              className="btn danger"
              onClick={() => cancelJob(jobId).catch(() => undefined)}
            >
              Cancel
            </button>
          )}
          {job.status === 'done' && (
            <>
              <button
                type="button"
                className="btn"
                onClick={() => window.open(reportUrl(jobId), '_blank')}
              >
                Open standalone report
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  window.location.href = downloadUrl(jobId);
                }}
              >
                Download HTML
              </button>
            </>
          )}
          <button type="button" className="btn" onClick={goToNew}>
            New comparison
          </button>
        </div>
      </div>

      {job.error && (
        <div className="banner err">
          <strong>{job.status === 'cancelled' ? 'Cancelled' : 'This comparison failed'}</strong>
          <pre>{job.error}</pre>
        </div>
      )}

      <Workflow job={job} />
      <PipelineLog logs={logs} />
      {report && <Results report={report} thumbs={thumbs} />}
    </>
  );
}
