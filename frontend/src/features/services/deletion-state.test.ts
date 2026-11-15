import { describe, expect, it } from 'vitest';
import { createQueryClient } from '../../app/query-client';
import { service } from '../../test/server';
import {
  deletionState,
  removeDeletedItems,
  removeServiceFromLists,
  serviceRead,
} from './deletion-state';

describe('deletion cache invariants', () => {
  it('updates matching totals across pages and preserves pagination and unrelated filters', () => {
    const client = createQueryClient();
    const other = { ...service, id: 'other' };
    const all = ['services', 'list', { limit: 20, offset: 20, isActive: undefined }];
    const active = ['services', 'list', { limit: 20, offset: 0, isActive: true }];
    const inactive = ['services', 'list', { limit: 10, offset: 0, isActive: false }];
    client.setQueryData(all, { items: [other], total: 22, limit: 20, offset: 20 });
    client.setQueryData(active, { items: [service, other], total: 2, limit: 20, offset: 0 });
    client.setQueryData(inactive, { items: [], total: 0, limit: 10, offset: 0 });
    client.setQueryData(['services', 'total', 'all'], {
      items: [service],
      total: 22,
      limit: 1,
      offset: 0,
    });
    client.setQueryData(['services', 'total', true], {
      items: [service],
      total: 2,
      limit: 1,
      offset: 0,
    });
    removeServiceFromLists(client, service);
    expect(client.getQueryData(all)).toEqual({ items: [other], total: 21, limit: 20, offset: 20 });
    expect(client.getQueryData(active)).toEqual({ items: [other], total: 1, limit: 20, offset: 0 });
    expect(client.getQueryData(inactive)).toEqual({ items: [], total: 0, limit: 10, offset: 0 });
    expect(client.getQueryData(['services', 'total', 'all'])).toMatchObject({ total: 21 });
    expect(client.getQueryData(['services', 'total', true])).toMatchObject({ total: 1 });
    client.clear();
  });
  it('filters late responses and scopes guards to their QueryClient', async () => {
    const client = createQueryClient();
    const otherClient = createQueryClient();
    const state = deletionState(client);
    state.begin(service.id);
    let calls = 0;
    await expect(
      serviceRead(client, service.id, async () => {
        calls++;
      }),
    ).rejects.toBeDefined();
    expect(calls).toBe(0);
    state.complete(service.id);
    state.finish(service.id);
    expect(
      removeDeletedItems(client, { items: [service], total: 1, limit: 20, offset: 0 }),
    ).toEqual({ items: [], total: 0, limit: 20, offset: 0 });
    await serviceRead(otherClient, service.id, async () => {
      calls++;
    });
    await serviceRead(
      client,
      service.id,
      async () => {
        calls++;
      },
      true,
    );
    expect(calls).toBe(2);
    client.clear();
    otherClient.clear();
  });
});
