/** A shared, abortable semaphore: obsolete queued work never reaches the network. */
export function createLimiter(max: number) {
  let active = 0;
  const queue: Array<() => void> = [];
  function release() {
    active--;
    queue.shift()?.();
  }
  return async function run<T>(task: () => Promise<T>, signal: AbortSignal): Promise<T> {
    signal.throwIfAborted();
    await new Promise<void>((resolve, reject) => {
      const start = () => {
        signal.removeEventListener('abort', abort);
        active++;
        resolve();
      };
      const abort = () => {
        const index = queue.indexOf(start);
        if (index !== -1) queue.splice(index, 1);
        reject(signal.reason);
      };
      if (active < max) start();
      else {
        queue.push(start);
        signal.addEventListener('abort', abort, { once: true });
      }
    });
    try {
      signal.throwIfAborted();
      return await task();
    } finally {
      release();
    }
  };
}
