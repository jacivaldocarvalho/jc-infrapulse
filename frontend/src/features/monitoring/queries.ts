import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Window } from '../../shared/api/client';
import { splitWindow } from './windows';
import { serviceRead, useServiceDeletions } from '../services/deletion-state';
const historical = {
  staleTime: Infinity,
  refetchInterval: false as const,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
};
export function useMetrics(id: string, window: Window, enabled: boolean) {
  const client = useQueryClient();
  const deletions = useServiceDeletions();
  return useQuery({
    queryKey: ['monitoring', id, 'metrics', window],
    queryFn: ({ signal }) => serviceRead(client, id, () => api.metrics(id, window, signal)),
    enabled: enabled && !deletions.pending.has(id) && !deletions.removed.has(id),
    ...historical,
  });
}
export function useChecks(id: string, window: Window, page: number, enabled: boolean) {
  const client = useQueryClient();
  const deletions = useServiceDeletions();
  return useQuery({
    queryKey: ['monitoring', id, 'checks', window, page],
    queryFn: ({ signal }) =>
      serviceRead(client, id, () =>
        api.checks(id, { ...window, limit: 20, offset: (page - 1) * 20 }, signal),
      ),
    enabled: enabled && !deletions.pending.has(id) && !deletions.removed.has(id),
    ...historical,
  });
}
export function useSeries(id: string, window: Window) {
  const client = useQueryClient();
  const deletions = useServiceDeletions();
  return useQuery({
    queryKey: ['monitoring', id, 'series', window],
    enabled: !deletions.pending.has(id) && !deletions.removed.has(id),
    queryFn: async ({ signal }) => {
      const controller = new AbortController();
      const abort = () => controller.abort(signal.reason);
      signal.addEventListener('abort', abort, { once: true });
      try {
        signal.throwIfAborted();
        return await Promise.all(
          splitWindow(window).map((bucket) =>
            serviceRead(client, id, () => api.metrics(id, bucket, controller.signal)),
          ),
        );
      } catch (error) {
        controller.abort();
        throw error;
      } finally {
        signal.removeEventListener('abort', abort);
      }
    },
    ...historical,
  });
}
