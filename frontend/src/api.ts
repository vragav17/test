import type {
  BrowseResult, Health, Job, JobListEntry, NewJobRequest, RegionThumbs, Report,
} from './types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail;
    } catch {
      // Error body was not JSON; the status text is the best we have.
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const getHealth = () => request<Health>('/api/health');
export const listJobs = () => request<JobListEntry[]>('/api/jobs');
export const getJob = (id: string) => request<Job>(`/api/jobs/${id}`);
export const getReport = (id: string) => request<Report>(`/api/jobs/${id}/report.json`);
export const getLogHistory = (id: string) =>
  request<Array<{ type: string; lane?: string; line?: string; message?: string }>>(
    `/api/jobs/${id}/log`,
  );

export const getThumbs = (id: string) =>
  request<{ regions: RegionThumbs[] }>(`/api/jobs/${id}/thumbs.json`).then((r) => r.regions);

export const browse = (path: string) =>
  request<BrowseResult>(`/api/browse?path=${encodeURIComponent(path)}`);

export const createJob = (body: NewJobRequest) =>
  request<{ id: string }>('/api/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

export const cancelJob = (id: string) =>
  request<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: 'POST' });

export const deleteJob = (id: string) =>
  request<{ ok: boolean }>(`/api/jobs/${id}`, { method: 'DELETE' });

export const reportUrl = (id: string) => `/api/jobs/${id}/report.html`;
export const downloadUrl = (id: string) => `/api/jobs/${id}/download`;
