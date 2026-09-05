import type { RegionType } from './types';

export const TYPE_COLOR: Record<RegionType, string> = {
  delete: '#e5484d',
  insert: '#46a758',
  replace: '#f5a524',
  audio_changed: '#8e6fd8',
};

export const TYPE_LABEL: Record<RegionType, string> = {
  delete: 'Removed',
  insert: 'Added',
  replace: 'Replaced',
  audio_changed: 'Audio changed',
};

export const TYPE_BLURB: Record<RegionType, string> = {
  delete: 'Present in A, absent from B.',
  insert: 'Absent from A, present in B.',
  replace: 'Both versions have content here, but the picture differs.',
  audio_changed: 'Picture matches shot for shot; the audio does not.',
};

export const REGION_TYPES: RegionType[] = ['delete', 'insert', 'replace', 'audio_changed'];

/** HH:MM:SS.mmm — matches format_tc() in vdiff_common.py. */
export function fmtTc(seconds: number): string {
  const s = Math.max(0, Number(seconds) || 0);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${sec.toFixed(3).padStart(6, '0')}`;
}

/** m:ss.s — compact enough for a ruler tick. */
export function shortTc(seconds: number): string {
  const s = Math.max(0, Number(seconds) || 0);
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}`;
}

export function fmtBytes(n?: number): string {
  if (!n) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function ago(ts: number | null): string {
  if (!ts) return '';
  const d = Math.max(0, Date.now() / 1000 - ts);
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}
