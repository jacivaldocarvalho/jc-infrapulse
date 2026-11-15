import { http, HttpResponse } from 'msw';
import { server } from '../../test/server';
import { describe, expect, it } from 'vitest';
import { api } from './client';
import { ApiError } from './errors';
describe('real contract client', () => {
  it('handles 204 deletion without parsing JSON', async () => {
    server.use(http.delete('*/api/v1/services/:id', () => new HttpResponse(null, { status: 204 })));
    await expect(api.remove('example')).resolves.toBeUndefined();
  });
  it('normalizes validation details without including rejected input', async () => {
    server.use(
      http.post('*/api/v1/services', () =>
        HttpResponse.json(
          { detail: [{ loc: ['body', 'url'], msg: 'Invalid URL', input: 'secret' }] },
          { status: 422 },
        ),
      ),
    );
    await expect(
      api.create({
        name: 'Test',
        url: 'invalid',
        check_interval_seconds: 60,
        timeout_seconds: 5,
        is_active: true,
      }),
    ).rejects.toMatchObject({ status: 422, fields: { url: 'Invalid URL' } });
    expect(new ApiError(500, { detail: 'database secret' }).message).not.toContain('secret');
  });
  it('cancels obsolete requests', async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(api.services({ limit: 20, offset: 0 }, controller.signal)).rejects.toThrow();
  });
});
