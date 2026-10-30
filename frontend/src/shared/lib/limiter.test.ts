import { describe, expect, it } from 'vitest';
import { createLimiter } from './limiter';
describe('historical metrics concurrency', () => {
  it('never runs more than four requests, including overlapping batches', async () => {
    const run = createLimiter(4);
    let active = 0;
    let peak = 0;
    const task = () =>
      run(async () => {
        peak = Math.max(peak, ++active);
        await new Promise((resolve) => setTimeout(resolve, 4));
        active--;
      }, new AbortController().signal);
    await Promise.all(Array.from({ length: 24 }, task));
    expect(peak).toBe(4);
  });
  it('removes aborted queued tasks without executing them', async () => {
    const run = createLimiter(1);
    let finish!: () => void;
    const first = run(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
      new AbortController().signal,
    );
    await Promise.resolve();
    const controller = new AbortController();
    let executed = false;
    const queued = run(async () => {
      executed = true;
    }, controller.signal);
    controller.abort();
    await expect(queued).rejects.toThrow();
    finish();
    await first;
    expect(executed).toBe(false);
  });
});
