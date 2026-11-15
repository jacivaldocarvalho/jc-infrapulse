import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type Service } from '../../shared/api/client';
import {
  removeDeletedItems,
  serviceRead,
  useServiceAccess,
  useServiceDeletions,
} from './deletion-state';
export function useServices(
  limit: number,
  offset: number,
  isActive: boolean | undefined,
  live: boolean,
) {
  const client = useQueryClient();
  return useQuery({
    queryKey: ['services', 'list', { limit, offset, isActive }],
    queryFn: async ({ signal }) =>
      removeDeletedItems(
        client,
        await api.services({ limit, offset, is_active: isActive }, signal),
      ),
    refetchInterval: live ? 30_000 : false,
  });
}
export function useVisibleStatuses(services: Service[], live: boolean) {
  const client = useQueryClient();
  const deletions = useServiceDeletions();
  return useQueries({
    queries: services.map((service) => ({
      queryKey: ['monitoring', service.id, 'status'],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        serviceRead(client, service.id, () => api.status(service.id, signal)),
      enabled: !deletions.pending.has(service.id) && !deletions.removed.has(service.id),
      refetchInterval: live && service.is_active ? 30_000 : false,
    })),
  });
}
export function useService(id: string) {
  const client = useQueryClient();
  const access = useServiceAccess(id);
  return useQuery({
    queryKey: ['services', 'detail', id],
    queryFn: ({ signal }) =>
      serviceRead(client, id, () => api.service(id, signal), access.allowRemoved),
    enabled: !access.blocked,
  });
}
export function useGlobalTotals(live: boolean) {
  const client = useQueryClient();
  return useQueries({
    queries: [undefined, true, false].map((isActive) => ({
      queryKey: ['services', 'total', isActive ?? 'all'],
      queryFn: async ({ signal }: { signal: AbortSignal }) =>
        removeDeletedItems(
          client,
          await api.services({ limit: 1, offset: 0, is_active: isActive }, signal),
        ),
      refetchInterval: live ? 30_000 : false,
    })),
  });
}
