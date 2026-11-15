import createClient from 'openapi-fetch';
import type { components, paths } from './generated';
import { ApiError } from './errors';
import { createLimiter } from '../lib/limiter';
export type ServicePage = components['schemas']['ServicePage'];
export type Service = components['schemas']['ServiceResponse'];
export type ServiceInput = components['schemas']['ServiceCreate'];
export type ServicePatch = components['schemas']['ServiceUpdate'];
export type CheckStatus = components['schemas']['CheckStatus'];
export type Metrics = components['schemas']['ServiceMetrics'];
export type Window = { start: string; end: string };
const client = createClient<paths>({
  fetch: (request) => globalThis.fetch(request),
  baseUrl: window.location.origin,
});
const limitMetrics = createLimiter(4);
function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (!result.response.ok) throw new ApiError(result.response.status, result.error);
  return result.data as T;
}
export const api = {
  async services(
    query: { limit: number; offset: number; is_active?: boolean },
    signal: AbortSignal,
  ) {
    return unwrap(await client.GET('/api/v1/services', { params: { query }, signal }));
  },
  async service(id: string, signal: AbortSignal) {
    return unwrap(
      await client.GET('/api/v1/services/{service_id}', {
        params: { path: { service_id: id } },
        signal,
      }),
    );
  },
  async status(id: string, signal: AbortSignal) {
    return unwrap(
      await client.GET('/api/v1/services/{service_id}/status', {
        params: { path: { service_id: id } },
        signal,
      }),
    );
  },
  async metrics(id: string, window: Window, signal: AbortSignal) {
    return limitMetrics(
      async () =>
        unwrap(
          await client.GET('/api/v1/services/{service_id}/metrics', {
            params: { path: { service_id: id }, query: window },
            signal,
          }),
        ),
      signal,
    );
  },
  async checks(id: string, query: Window & { limit: number; offset: number }, signal: AbortSignal) {
    return unwrap(
      await client.GET('/api/v1/services/{service_id}/checks', {
        params: { path: { service_id: id }, query },
        signal,
      }),
    );
  },
  async create(body: ServiceInput) {
    return unwrap(await client.POST('/api/v1/services', { body }));
  },
  async update(id: string, body: ServicePatch) {
    return unwrap(
      await client.PATCH('/api/v1/services/{service_id}', {
        params: { path: { service_id: id } },
        body,
      }),
    );
  },
  async remove(id: string) {
    const result = await client.DELETE('/api/v1/services/{service_id}', {
      params: { path: { service_id: id } },
    });
    if (!result.response.ok) throw new ApiError(result.response.status, result.error);
  },
};
