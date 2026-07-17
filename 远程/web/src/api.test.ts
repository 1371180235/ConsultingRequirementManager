// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, setCsrfToken } from './api';

afterEach(() => {
  setCsrfToken(undefined);
  vi.unstubAllGlobals();
});

describe('API client', () => {
  it('sends credentials and the CSRF token on write requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    setCsrfToken('csrf-token');

    await api.post('/api/projects', { name: '测试项目' });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe('include');
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf-token');
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json');
  });

  it('surfaces FastAPI structured error messages instead of object text', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: { code: 'VERSION_LOCKED', message: '版本已冻结' },
    }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    })));

    await expect(api.patch('/api/versions/1', { name: '新名称' })).rejects.toMatchObject({
      status: 409,
      message: '版本已冻结（VERSION_LOCKED）',
    });
  });

  it('returns CSV responses as text', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('\uFEFF编号,名称\r\n1,项目', {
      status: 200,
      headers: { 'Content-Type': 'text/csv; charset=utf-8' },
    })));

    await expect(api.get<string>('/api/exports/projects.csv')).resolves.toContain('编号,名称');
  });
});
