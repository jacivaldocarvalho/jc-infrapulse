import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import type { Service } from '../shared/api/client';
export const service: Service = {
  id: '2bcab680-66ce-4e10-80ca-c08a2741cb01',
  name: 'Payments API',
  url: 'https://example.com/health',
  check_interval_seconds: 60,
  timeout_seconds: 5,
  is_active: true,
  created_at: '2026-10-01T12:00:00Z',
  updated_at: '2026-10-01T12:00:00Z',
};
export const server = setupServer(
  http.get('*/api/v1/services', () =>
    HttpResponse.json({ items: [], total: 0, limit: 20, offset: 0 }),
  ),
);
