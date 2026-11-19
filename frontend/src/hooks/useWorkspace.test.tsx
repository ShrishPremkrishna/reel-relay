import { StrictMode, type ReactNode } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useWorkspace } from './useWorkspace';
import { readiness, response, setup, workspace } from '../test/fixtures';

function byPath(path: string) {
  return path === '/api/state' ? workspace : path === '/api/setup' ? setup : readiness;
}
describe('workspace refresh coordination', () => {
  it('loads immediately under React StrictMode effect replay', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => Promise.resolve(response(byPath(path)))),
    );
    const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;
    const { result } = renderHook(() => useWorkspace(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.state?.candidates).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });
  it('fetches a new snapshot after a mutation even if an older poll is pending', async () => {
    let release: (value: Response) => void = () => {};
    let stateCalls = 0;
    const oldState = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetcher = vi.fn((path: string) => {
      if (path === '/api/state') {
        stateCalls++;
        return stateCalls === 1
          ? oldState
          : Promise.resolve(response({ ...workspace, counts: { ready: 2 } }));
      }
      return Promise.resolve(response(byPath(path)));
    });
    vi.stubGlobal('fetch', fetcher);
    const { result } = renderHook(() => useWorkspace());
    await act(async () => {
      const refresh = result.current.reload();
      release(response(workspace));
      await refresh;
    });
    expect(stateCalls).toBe(2);
    expect(result.current.state?.counts.ready).toBe(2);
    expect(result.current.refreshing).toBe(false);
  });
  it('retains the last good snapshot after a network interruption', async () => {
    let offline = false;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        offline
          ? Promise.reject(new TypeError('Failed to fetch'))
          : Promise.resolve(response(byPath(path))),
      ),
    );
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.state).not.toBeNull());
    offline = true;
    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.error?.message).toContain('Cannot reach');
    expect(result.current.state?.candidates[0].id).toBe(workspace.candidates[0].id);
  });

  it('reports a failed confirming refresh to settings without erasing the last snapshot', async () => {
    let offline = false;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        offline
          ? Promise.reject(new TypeError('offline'))
          : Promise.resolve(response(byPath(path))),
      ),
    );
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.setup).not.toBeNull());
    offline = true;
    await act(async () => {
      await expect(result.current.reloadChecked()).rejects.toThrow('Cannot reach');
    });
    expect(result.current.setup?.model).toBe(setup.model);
    offline = false;
    await act(async () => {
      await result.current.reloadChecked();
    });
    expect(result.current.error).toBeNull();
  });
  it('asks for a session on authentication failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(response({ error: 'Authentication required' }, 401)),
    );
    const { result } = renderHook(() => useWorkspace());
    await waitFor(() => expect(result.current.needsLogin).toBe(true));
    expect(result.current.state).toBeNull();
  });
});
