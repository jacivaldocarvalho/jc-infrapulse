import { CancelledError, type QueryClient, useQueryClient } from '@tanstack/react-query';
import { useState, useSyncExternalStore } from 'react';
import type { Service, ServicePage } from '../../shared/api/client';

type Snapshot = { pending: ReadonlySet<string>; removed: ReadonlySet<string> };
function createState() {
  let snapshot: Snapshot = { pending: new Set(), removed: new Set() };
  const listeners = new Set<() => void>();
  const update = (next: Snapshot) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    begin(id: string) {
      if (snapshot.pending.has(id) || snapshot.removed.has(id))
        throw new Error('A exclusão deste serviço já foi iniciada.');
      update({ ...snapshot, pending: new Set([...snapshot.pending, id]) });
    },
    complete(id: string) {
      update({ ...snapshot, removed: new Set([...snapshot.removed, id]) });
    },
    finish(id: string) {
      if (!snapshot.pending.has(id)) return;
      const pending = new Set(snapshot.pending);
      pending.delete(id);
      update({ ...snapshot, pending });
    },
  };
}
// Successful IDs survive cache eviction only for this QueryClient session, so
// late list responses cannot recreate observers. This state is never persisted.
const states = new WeakMap<QueryClient, ReturnType<typeof createState>>();
export function deletionState(client: QueryClient) {
  let state = states.get(client);
  if (!state) {
    state = createState();
    states.set(client, state);
  }
  return state;
}
export function useServiceDeletions() {
  const state = deletionState(useQueryClient());
  return useSyncExternalStore(state.subscribe, state.getSnapshot, state.getSnapshot);
}
export function serviceBlocked(client: QueryClient, id: string, allowRemoved = false): boolean {
  const state = deletionState(client).getSnapshot();
  return state.pending.has(id) || (!allowRemoved && state.removed.has(id));
}
export function serviceRead<T>(
  client: QueryClient,
  id: string,
  task: () => Promise<T>,
  allowRemoved = false,
): Promise<T> {
  // Also guard manual refetch and observer callbacks queued before React commits.
  if (serviceBlocked(client, id, allowRemoved))
    return Promise.reject(new CancelledError({ silent: true }));
  return task();
}
export function useServiceAccess(id: string) {
  const snapshot = useServiceDeletions();
  const [entry, setEntry] = useState(() => ({ id, allowRemoved: snapshot.removed.has(id) }));
  if (entry.id !== id) setEntry({ id, allowRemoved: snapshot.removed.has(id) });
  // Existing detail observers stay blocked after success, even during lazy navigation.
  // A fresh direct visit is allowed to reach the API and receive its ordinary 404.
  return {
    allowRemoved: entry.allowRemoved,
    blocked: snapshot.pending.has(id) || (snapshot.removed.has(id) && !entry.allowRemoved),
  };
}
export function removeDeletedItems(client: QueryClient, page: ServicePage): ServicePage {
  const removed = deletionState(client).getSnapshot().removed;
  const items = page.items.filter((item) => !removed.has(item.id));
  return items.length === page.items.length
    ? page
    : { ...page, items, total: Math.max(0, page.total - (page.items.length - items.length)) };
}
export function removeServiceFromLists(client: QueryClient, service: Service) {
  for (const [key, page] of client.getQueriesData<ServicePage>({
    queryKey: ['services', 'list'],
  })) {
    if (!page) continue;
    const filter = (key[2] as { isActive?: boolean }).isActive;
    if (filter !== undefined && filter !== service.is_active) continue;
    client.setQueryData<ServicePage>(key, {
      ...page,
      items: page.items.filter((item) => item.id !== service.id),
      total: Math.max(0, page.total - 1),
    });
  }
  for (const [key, page] of client.getQueriesData<ServicePage>({
    queryKey: ['services', 'total'],
  })) {
    if (!page || (key[2] !== 'all' && key[2] !== service.is_active)) continue;
    client.setQueryData<ServicePage>(key, {
      ...page,
      items: page.items.filter((item) => item.id !== service.id),
      total: Math.max(0, page.total - 1),
    });
  }
}
