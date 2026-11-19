import { describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { mutate, request, safeExternalUrl } from './api';
import { response } from '../test/fixtures';

describe('workspace API boundary', () => {
  it('shows safe field-level validation details alongside the server summary', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          {
            error: 'Check the highlighted settings',
            detail: [
              {
                loc: ['body', 'editing', 'max_seconds'],
                msg: 'Input should be less than or equal to 90',
                type: 'less_than_equal',
              },
            ],
          },
          422,
        ),
      ),
    );
    await expect(mutate('/api/setup', {})).rejects.toThrow(
      'editing.max_seconds: Input should be less than or equal to 90',
    );
  });

  it('sends JSON mutations to the same origin with the CSRF header', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    await mutate('/api/approve', { id: 'clip', version: 42 }, 'csrf');
    expect(fetcher).toHaveBeenCalledWith(
      '/api/approve',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
          'X-Relay-Token': 'csrf',
        }),
        body: '{"id":"clip","version":42}',
      }),
    );
  });
  it('rejects off-origin application requests before sending any data', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    await expect(mutate('//elsewhere.example/api', { token: 'private' })).rejects.toThrow(
      'Only this workspace',
    );
    await expect(request('https://elsewhere.example/api')).rejects.toThrow('Only this workspace');
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('turns invalid API snapshots into an actionable version error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ wrong: true })));
    await expect(request('/api/state', z.object({ items: z.array(z.string()) }))).rejects.toThrow(
      'versions do not match',
    );
  });
  it('preserves conflict status and never retries an uncertain mutation', async () => {
    const fetcher = vi.fn().mockResolvedValue(response({ error: 'Export changed' }, 409));
    vi.stubGlobal('fetch', fetcher);
    await expect(mutate('/api/approve', { id: 'clip', version: 1 }, 'csrf')).rejects.toMatchObject({
      status: 409,
      message: 'Export changed',
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('does not expose proxy HTML to the operator', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response('<html>private upstream information</html>', { status: 502 }),
        ),
    );
    await expect(request('/api/state')).rejects.toThrow('unreadable response');
  });
  it('allows only HTTPS destinations for platform links', () => {
    expect(safeExternalUrl('javascript:alert(1)')).toBeUndefined();
    expect(safeExternalUrl('//example.com')).toBeUndefined();
    expect(safeExternalUrl('https://www.instagram.com/reel/abc/')).toBe(
      'https://www.instagram.com/reel/abc/',
    );
  });
});
