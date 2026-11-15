import { MantineProvider } from '@mantine/core';
import { QueryClientProvider, focusManager } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { PropsWithChildren } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { createQueryClient } from '../../app/query-client';
import { server, service } from '../../test/server';
import { useMetrics, useSeries } from './queries';
const window = { start: '2026-10-01T00:00:00.000Z', end: '2026-10-02T00:00:00.000Z' };
function wrapper({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={createQueryClient()}>
      <MantineProvider>{children}</MantineProvider>
    </QueryClientProvider>
  );
}
describe('historical query behavior', () => {
  it('keeps null metrics and never polls a fixed historical period', async () => {
    let calls = 0;
    server.use(
      http.get('*/api/v1/services/:id/metrics', () => {
        calls++;
        return HttpResponse.json({
          service_id: service.id,
          ...window,
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
    );
    const { result, unmount } = renderHook(() => useMetrics(service.id, window, true), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.uptime_percentage).toBeNull();
    vi.useFakeTimers();
    try {
      await act(async () => {
        focusManager.setFocused(false);
        await vi.advanceTimersByTimeAsync(180_000);
        focusManager.setFocused(true);
      });
      expect(calls).toBe(1);
    } finally {
      unmount();
      vi.useRealTimers();
      focusManager.setFocused(undefined);
    }
  });
  it('retrieves exactly twelve windows with at most four active requests', async () => {
    let active = 0;
    let peak = 0;
    let calls = 0;
    server.use(
      http.get('*/api/v1/services/:id/metrics', async ({ request }) => {
        calls++;
        peak = Math.max(peak, ++active);
        await new Promise((resolve) => setTimeout(resolve, 15));
        active--;
        const params = new URL(request.url).searchParams;
        return HttpResponse.json({
          service_id: service.id,
          start: params.get('start'),
          end: params.get('end'),
          total_checks: 1,
          up_checks: 1,
          down_checks: 0,
          uptime_percentage: 100,
          average_response_time_ms: 7,
          p95_response_time_ms: 7,
          last_status: 'UP',
          last_checked_at: params.get('start'),
        });
      }),
    );
    const { result } = renderHook(() => useSeries(service.id, window), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(calls).toBe(12);
    expect(peak).toBeLessThanOrEqual(4);
    expect(result.current.data).toHaveLength(12);
  });
});
