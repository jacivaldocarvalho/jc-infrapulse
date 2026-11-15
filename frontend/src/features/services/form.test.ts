import { describe, expect, it } from 'vitest';
import { defaults, servicePatch, validateService } from './form';
import { service } from '../../test/server';
describe('registry form rules', () => {
  it('rejects empty names, credentials, invalid integers and timeout >= interval', () => {
    expect(
      validateService({
        ...defaults,
        url: 'https://user:password@example.com',
        check_interval_seconds: 10,
        timeout_seconds: 10,
      }),
    ).toEqual({
      name: expect.any(String),
      url: expect.any(String),
      timeout_seconds: expect.any(String),
    });
    expect(
      validateService({
        ...defaults,
        name: 'API',
        url: 'https://example.com',
        timeout_seconds: '',
        check_interval_seconds: 2147483648,
      }),
    ).toHaveProperty('check_interval_seconds');
  });
  it('sends only modified normalized fields, never null', () => {
    expect(servicePatch(service, { ...service, name: '  New API  ' })).toEqual({ name: 'New API' });
    expect(servicePatch(service, service)).toEqual({});
  });
});
