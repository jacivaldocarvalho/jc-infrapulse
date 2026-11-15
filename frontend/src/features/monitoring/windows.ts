import type { Window } from '../../shared/api/client';
export const MAX_WINDOW_MS = 90 * 24 * 60 * 60 * 1000;
export function validWindow(start: string, end: string): boolean {
  const zone = /(?:Z|[+-]\d{2}:\d{2})$/i;
  if (!zone.test(start) || !zone.test(end)) return false;
  const duration = Date.parse(end) - Date.parse(start);
  return Number.isFinite(duration) && duration > 0 && duration <= MAX_WINDOW_MS;
}
export function recentWindow(hours = 24, now = Date.now()): Window {
  return {
    start: new Date(now - hours * 3_600_000).toISOString(),
    end: new Date(now).toISOString(),
  };
}
export function splitWindow(window: Window, count = 12): Window[] {
  const start = Date.parse(window.start);
  const duration = Date.parse(window.end) - start;
  const buckets = Math.min(count, Math.max(1, duration));
  return Array.from({ length: buckets }, (_, index) => ({
    start: new Date(start + (duration * index) / buckets).toISOString(),
    end: new Date(start + (duration * (index + 1)) / buckets).toISOString(),
  }));
}
export function localInput(iso: string): string {
  const date = new Date(iso);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}
