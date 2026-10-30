import { describe, expect, it } from 'vitest';
import { recentWindow, splitWindow, validWindow } from './windows';
import { number, stateLabel } from '../../shared/lib/presentation';
describe('monitoring semantics', () => {
  it('preserves no data and distinguishes all technical failures', () => {
    expect(number(null, '%')).toBe('—');
    expect(number(0, '%')).toBe('0 %');
    expect(stateLabel(null)).toBe('Sem dados');
    expect(stateLabel('TIMEOUT')).toBe('DOWN');
    expect(stateLabel('UP')).toBe('UP');
  });
  it('splits the exact period into twelve contiguous windows', () => {
    const window = recentWindow(24, Date.parse('2026-10-30T12:00:00Z'));
    const buckets = splitWindow(window);
    expect(buckets).toHaveLength(12);
    expect(buckets[0].start).toBe(window.start);
    expect(buckets[11].end).toBe(window.end);
    buckets.slice(1).forEach((item, index) => expect(item.start).toBe(buckets[index].end));
  });
  it('avoids empty buckets for subsecond periods', () => {
    expect(
      splitWindow({ start: '2026-01-01T00:00:00.000Z', end: '2026-01-01T00:00:00.001Z' }),
    ).toHaveLength(1);
  });
  it('rejects invalid periods and accepts at most 90 days', () => {
    const start = '2026-01-01T00:00:00Z';
    expect(validWindow(start, start)).toBe(false);
    expect(validWindow(start, '2026-04-01T00:00:00Z')).toBe(true);
    expect(validWindow(start, '2026-04-02T00:00:00Z')).toBe(false);
    expect(validWindow('', '')).toBe(false);
    expect(validWindow('2026-01-01T00:00:00', '2026-01-02T00:00:00')).toBe(false);
  });
});
