import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it, vi } from 'vitest';
import { Button, Text } from '@mantine/core';
import { Link, Route, Routes } from 'react-router';
import { DeleteService } from './DeleteService';
import { useServices, useVisibleStatuses } from './queries';
import ServiceDetailPage from '../../pages/ServiceDetailPage';
import ServicesPage from '../../pages/ServicesPage';
import { renderPage } from '../../test/render';
import { server, service } from '../../test/server';

const other = { ...service, id: '2bcab680-66ce-4e10-80ca-c08a2741cb02', name: 'Other API' };
function PollingHarness() {
  const queries = useVisibleStatuses([service, other], true);
  return (
    <>
      <DeleteService service={service} />
      <Button
        onClick={() => {
          void queries[0].refetch().catch(() => {});
        }}
      >
        Manual target
      </Button>
    </>
  );
}
function SlowListDestination() {
  useServices(20, 0, undefined, false);
  return (
    <>
      <Text>List destination</Text>
      <Link to={`/services/${service.id}`}>Direct visit</Link>
    </>
  );
}
function acceleratePolling() {
  const original = globalThis.setInterval;
  return vi
    .spyOn(globalThis, 'setInterval')
    .mockImplementation((callback, delay, ...args) =>
      original(callback, delay === 30_000 ? 20 : delay, ...args),
    );
}
describe('deletion query coordination', () => {
  it('does not start status queries after DELETE while the list refetch is delayed', async () => {
    let deleted = false;
    let refetchStarted = false;
    let statusAfterDelete = 0;
    let releaseList!: () => void;
    const listGate = new Promise<void>((resolve) => {
      releaseList = resolve;
    });
    server.use(
      http.get('*/api/v1/services', async () => {
        if (deleted) {
          refetchStarted = true;
          await listGate;
        }
        return HttpResponse.json({
          items: deleted ? [] : [service],
          total: deleted ? 0 : 1,
          limit: 20,
          offset: 0,
        });
      }),
      http.get('*/api/v1/services/:id/status', () => {
        if (deleted) statusAfterDelete++;
        return HttpResponse.json(
          deleted
            ? { detail: 'Service not found' }
            : { service_id: service.id, last_status: 'UP', last_checked_at: service.created_at },
          { status: deleted ? 404 : 200 },
        );
      }),
      http.delete('*/api/v1/services/:id', () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { client } = renderPage(<ServicesPage />);
    try {
      await screen.findByText('✓ UP 1');
      await userEvent.click(screen.getByRole('button', { name: `Excluir ${service.name}` }));
      await userEvent.click(await screen.findByRole('button', { name: 'Excluir definitivamente' }));
      await waitFor(() => expect(refetchStarted).toBe(true));
      // Let both React commits and Query's scheduled observer notifications settle
      // while the server deliberately keeps the stale list revalidation pending.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 80));
      });
      expect(statusAfterDelete).toBe(0);
      expect(screen.queryByRole('link', { name: service.name })).not.toBeInTheDocument();
      releaseList();
      await screen.findByText('Nenhum serviço encontrado');
      expect(statusAfterDelete).toBe(0);
    } finally {
      releaseList();
      client.clear();
    }
  });
});

describe('deletion lifecycle regressions', () => {
  it('cancels pending status before DELETE, pauses polling/manual refresh and keeps other services running', async () => {
    const timer = acceleratePolling();
    let targetCalls = 0;
    let otherCalls = 0;
    let aborted = false;
    let deleteStarted = false;
    let abortedBeforeDelete = false;
    let releaseStatus!: () => void;
    let releaseDelete!: () => void;
    const statusGate = new Promise<void>((resolve) => {
      releaseStatus = resolve;
    });
    const deleteGate = new Promise<void>((resolve) => {
      releaseDelete = resolve;
    });
    server.use(
      http.get('*/api/v1/services/:id/status', async ({ params, request }) => {
        if (params.id === service.id) {
          targetCalls++;
          request.signal.addEventListener('abort', () => {
            aborted = true;
          });
          await statusGate;
        } else otherCalls++;
        return HttpResponse.json({
          service_id: params.id,
          last_status: 'UP',
          last_checked_at: service.created_at,
        });
      }),
      http.delete('*/api/v1/services/:id', async () => {
        deleteStarted = true;
        abortedBeforeDelete = aborted;
        await deleteGate;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { client, unmount } = renderPage(<PollingHarness />);
    try {
      await waitFor(() => expect(targetCalls).toBe(1));
      await userEvent.click(screen.getByRole('button', { name: `Excluir ${service.name}` }));
      await userEvent.click(await screen.findByRole('button', { name: 'Excluir definitivamente' }));
      await waitFor(() => expect(deleteStarted).toBe(true));
      expect(abortedBeforeDelete).toBe(true);
      const othersBefore = otherCalls;
      await userEvent.click(screen.getByRole('button', { name: 'Manual target' }));
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 100));
      });
      expect(targetCalls).toBe(1);
      expect(otherCalls).toBeGreaterThan(othersBefore);
      releaseStatus();
      releaseDelete();
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      await userEvent.click(screen.getByRole('button', { name: 'Manual target' }));
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 80));
      });
      expect(targetCalls).toBe(1);
    } finally {
      releaseStatus();
      releaseDelete();
      unmount();
      client.clear();
      timer.mockRestore();
    }
  });

  it('does not pause queries or call DELETE when the user cancels the dialog', async () => {
    const timer = acceleratePolling();
    let calls = 0;
    let deletes = 0;
    server.use(
      http.get('*/api/v1/services/:id/status', ({ params }) => {
        if (params.id === service.id) calls++;
        return HttpResponse.json({
          service_id: params.id,
          last_status: 'UP',
          last_checked_at: service.created_at,
        });
      }),
      http.delete('*/api/v1/services/:id', () => {
        deletes++;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { client, unmount } = renderPage(<PollingHarness />);
    try {
      await waitFor(() => expect(calls).toBeGreaterThan(0));
      await userEvent.click(screen.getByRole('button', { name: `Excluir ${service.name}` }));
      await userEvent.click(await screen.findByRole('button', { name: 'Cancelar' }));
      const before = calls;
      await waitFor(() => expect(calls).toBeGreaterThan(before));
      expect(deletes).toBe(0);
    } finally {
      unmount();
      client.clear();
      timer.mockRestore();
    }
  });

  it('preserves the row and resumes queries after a failed DELETE, then allows retry', async () => {
    let statusCalls = 0;
    let fail = true;
    let deleted = false;
    server.use(
      http.get('*/api/v1/services', () =>
        HttpResponse.json({
          items: deleted ? [] : [service],
          total: deleted ? 0 : 1,
          limit: 20,
          offset: 0,
        }),
      ),
      http.get('*/api/v1/services/:id/status', () => {
        statusCalls++;
        return HttpResponse.json({
          service_id: service.id,
          last_status: 'UP',
          last_checked_at: service.created_at,
        });
      }),
      http.delete('*/api/v1/services/:id', () => {
        if (fail) return new HttpResponse(null, { status: 503 });
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { client } = renderPage(<ServicesPage />);
    await screen.findByText('✓ UP 1');
    const before = statusCalls;
    await userEvent.click(screen.getByRole('button', { name: `Excluir ${service.name}` }));
    await userEvent.click(await screen.findByRole('button', { name: 'Excluir definitivamente' }));
    await screen.findByRole('alert');
    await waitFor(() => expect(statusCalls).toBeGreaterThan(before));
    expect(screen.getByRole('link', { name: service.name })).toBeInTheDocument();
    expect(
      client.getQueryData<{ total: number }>([
        'services',
        'list',
        { limit: 20, offset: 0, isActive: undefined },
      ])?.total,
    ).toBe(1);
    fail = false;
    await userEvent.click(screen.getByRole('button', { name: 'Excluir definitivamente' }));
    await screen.findByText('Nenhum serviço encontrado');
    client.clear();
  });

  it('navigates from detail before slow revalidation and permits a fresh direct visit to receive 404', async () => {
    let deleted = false;
    let postDeleteReads = 0;
    let revalidating = false;
    let releaseList!: () => void;
    const gate = new Promise<void>((resolve) => {
      releaseList = resolve;
    });
    server.use(
      http.get('*/api/v1/services', async () => {
        if (deleted) {
          revalidating = true;
          await gate;
        }
        return HttpResponse.json({
          items: deleted ? [] : [service],
          total: deleted ? 0 : 1,
          limit: 20,
          offset: 0,
        });
      }),
      http.get('*/api/v1/services/:id', () => {
        if (deleted) postDeleteReads++;
        return HttpResponse.json(deleted ? { detail: 'Service not found' } : service, {
          status: deleted ? 404 : 200,
        });
      }),
      http.get('*/api/v1/services/:id/status', () => {
        if (deleted) postDeleteReads++;
        return HttpResponse.json({
          service_id: service.id,
          last_status: 'UP',
          last_checked_at: service.created_at,
        });
      }),
      http.get('*/api/v1/services/:id/metrics', ({ request }) => {
        if (deleted) postDeleteReads++;
        const query = new URL(request.url).searchParams;
        return HttpResponse.json({
          service_id: service.id,
          start: query.get('start'),
          end: query.get('end'),
          total_checks: 0,
          up_checks: 0,
          down_checks: 0,
          uptime_percentage: null,
          average_response_time_ms: null,
          p95_response_time_ms: null,
          last_status: null,
          last_checked_at: null,
        });
      }),
      http.get('*/api/v1/services/:id/checks', () => {
        if (deleted) postDeleteReads++;
        return HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 });
      }),
      http.delete('*/api/v1/services/:id', () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const { client, unmount } = renderPage(
      <Routes>
        <Route path="/services/:id" element={<ServiceDetailPage />} />
        <Route path="/services" element={<SlowListDestination />} />
      </Routes>,
      `/services/${service.id}`,
    );
    try {
      await screen.findByRole('heading', { name: service.name });
      await screen.findByText('Nenhuma verificação no período');
      await userEvent.click(screen.getByRole('button', { name: `Excluir ${service.name}` }));
      await userEvent.click(await screen.findByRole('button', { name: 'Excluir definitivamente' }));
      await screen.findByText('List destination');
      await waitFor(() => expect(revalidating).toBe(true));
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 80));
      });
      expect(postDeleteReads).toBe(0);
      releaseList();
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 30));
      });
      await userEvent.click(screen.getByRole('link', { name: 'Direct visit' }));
      expect(await screen.findByRole('alert')).toHaveTextContent('Serviço não encontrado.');
      expect(postDeleteReads).toBe(1);
    } finally {
      releaseList();
      unmount();
      client.clear();
    }
  });
});
