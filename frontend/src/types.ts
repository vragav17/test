/** Types mirroring the FastAPI surface in app.py and the dataclasses in jobs.py. */

export type Lane = 'a' | 'b' | 'merge';

export type StageStatus =
  | 'queued' | 'running' | 'done' | 'cached' | 'failed' | 'skipped' | 'cancelled';

export type JobStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled';

export type RegionType = 'delete' | 'insert' | 'replace' | 'audio_changed';

export interface Stage {
  id: string;
  label: string;
  lane: Lane;
  status: StageStatus;
  elapsed: number | null;
  detail: string;
}

export interface JobSummary {
  region_count: number;
  alignment_score: number;
  counts: Record<RegionType, number>;
  duration_a: number;
  duration_b: number;
  shots_a: number;
  shots_b: number;
}

export interface Job {
  id: string;
  video_a: string;
  video_b: string;
  label_a: string;
  label_b: string;
  threshold: number;
  audio_threshold: number;
  explain: boolean;
  model: string;
  ollama_url: string;
  status: JobStatus;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  error: string | null;
  stages: Record<string, Stage>;
  summary: JobSummary | null;
}

export interface JobListEntry {
  id: string;
  label_a: string;
  label_b: string;
  status: JobStatus;
  created_at: number;
  finished_at: number | null;
  summary: JobSummary | null;
  explain: boolean;
}

export interface Region {
  type: RegionType;
  a_start: number;
  a_end: number;
  b_start: number;
  b_end: number;
  a_timecode: string;
  b_timecode: string;
  shot_count: number;
  thumbnail_count_a: number;
  thumbnail_count_b: number;
  description_a: string | null;
  description_b: string | null;
  explanation: string | null;
}

export interface VersionInfo {
  source: string;
  proxy: string | null;
  duration_seconds: number;
  shot_count: number;
}

export interface Report {
  version_a: VersionInfo;
  version_b: VersionInfo;
  alignment_score: number;
  audio_change_threshold: number;
  explained: boolean;
  region_count: number;
  summary: Record<RegionType, number>;
  regions: Region[];
}

export interface RegionThumbs {
  thumbnails_a: string[];
  thumbnails_b: string[];
}

export interface Health {
  ffmpeg: boolean;
  videotoolbox: boolean;
  ollama: boolean;
  models: string[];
  ffmpeg_error?: string;
}

export interface BrowseEntry {
  name: string;
  path: string;
  size?: number;
}

export interface BrowseResult {
  path: string;
  parent: string | null;
  dirs: BrowseEntry[];
  videos: BrowseEntry[];
}

export interface NewJobRequest {
  video_a: string;
  video_b: string;
  threshold: number;
  audio_threshold: number;
  explain: boolean;
  model: string;
  ollama_url?: string;
}

/** Events pushed over /api/jobs/{id}/events. */
export type JobEvent =
  | { type: 'started'; t: number }
  | { type: 'stage'; stage: string; status: StageStatus; detail: string; elapsed: number | null; t: number }
  | { type: 'log'; lane: Lane; line: string; t: number }
  | { type: 'error'; message: string; t: number }
  | { type: 'finished'; status: JobStatus; t: number }
  | { type: 'closed'; status: JobStatus; t: number };

export interface LogLine {
  lane: Lane;
  line: string;
  warn?: boolean;
}
