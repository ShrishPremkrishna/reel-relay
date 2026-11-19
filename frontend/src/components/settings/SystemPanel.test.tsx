import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SystemPanel } from './SystemPanel';
import { stoppedSystem } from '../../test/settingsFixtures';
import { readiness, response } from '../../test/fixtures';
const props = () => ({
  csrf: 'csrf-test',
  readiness,
  onChange: vi.fn().mockResolvedValue(undefined),
  notify: vi.fn(),
});
afterEach(() => vi.useRealTimers());
describe('workspace service lifecycle', () => {
  it('allows a stopped unmanaged workspace to start through the explicit service endpoint', async () => {
    const user = userEvent.setup(),
      p = props();
    const started = {
      ...stoppedSystem,
      managed: true,
      services: {
        ...stoppedSystem.services,
        capture: { enabled: true, state: 'starting', heartbeat_at: null },
      },
    };
    let startedRequested = false;
    const fetcher = vi.fn((_url: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        startedRequested = true;
        return Promise.resolve(response(started));
      }
      return Promise.resolve(response(startedRequested ? started : stoppedSystem));
    });
    vi.stubGlobal('fetch', fetcher);
    render(<SystemPanel {...p} />);
    await user.click(await screen.findByRole('button', { name: 'Start capture' }));
    expect(fetcher).toHaveBeenCalledWith(
      '/api/system/control',
      expect.objectContaining({
        body: JSON.stringify({ service: 'capture', enabled: true }),
        headers: expect.objectContaining({ 'X-Relay-Token': 'csrf-test' }),
      }),
    );
    expect(await screen.findByRole('button', { name: 'Starting…' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Pause capture' })).not.toBeInTheDocument();
    expect(p.onChange).toHaveBeenCalled();
  });
  it('pauses a running editor without changing publication', async () => {
    const user = userEvent.setup();
    const running = {
      ...stoppedSystem,
      managed: true,
      services: {
        ...stoppedSystem.services,
        editor: { enabled: true, state: 'running', heartbeat_at: 100 },
      },
    };
    const fetcher = vi.fn().mockResolvedValue(response(running));
    vi.stubGlobal('fetch', fetcher);
    render(<SystemPanel {...props()} />);
    await user.click(await screen.findByRole('button', { name: 'Pause editing' }));
    expect(fetcher).toHaveBeenCalledWith(
      '/api/system/control',
      expect.objectContaining({ body: JSON.stringify({ service: 'editor', enabled: false }) }),
    );
    expect(fetcher.mock.calls.some((c) => String(c[0]).includes('publishing'))).toBe(false);
  });
  it('installs the chosen speech model and refreshes readiness when it completes', async () => {
    vi.useFakeTimers();
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    let installation = { status: 'idle', model: null as string | null, message: '' };
    const p = props();
    const fetcher = vi.fn((url: string) =>
      Promise.resolve(
        response(
          url === '/api/system/install'
            ? { status: 'running', model: 'base', message: 'Downloading model' }
            : { ...stoppedSystem, installation },
        ),
      ),
    );
    vi.stubGlobal('fetch', fetcher);
    render(<SystemPanel {...p} />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole('button', { name: 'Download & install' })).toBeEnabled();
    fireEvent.change(screen.getByRole('combobox', { name: 'Transcription model' }), {
      target: { value: 'base' },
    });
    installation = { status: 'running', model: 'base', message: 'Downloading model' };
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Download & install' }));
    });
    expect(fetcher).toHaveBeenCalledWith(
      '/api/system/install',
      expect.objectContaining({ body: JSON.stringify({ model: 'base' }) }),
    );
    expect(screen.getByRole('button', { name: 'Installing…' })).toBeDisabled();
    installation = { status: 'succeeded', model: 'base', message: 'Ready' };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    vi.useRealTimers();
    await waitFor(() => expect(p.onChange).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: 'Download & install' })).toBeEnabled();
  });

  it('restores the active installation model on reload without overwriting a later idle choice', async () => {
    vi.useFakeTimers();
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
    let installation = { status: 'running', model: 'tiny', message: 'Downloading Tiny' };
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(response({ ...stoppedSystem, installation }))),
    );
    render(<SystemPanel {...props()} />);
    await act(async () => {
      await Promise.resolve();
    });
    const selector = screen.getByRole('combobox', { name: 'Transcription model' });
    expect(selector).toHaveValue('tiny');
    expect(selector).toBeDisabled();
    installation = { status: 'succeeded', model: 'tiny', message: 'Ready' };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(selector).toBeEnabled();
    fireEvent.change(selector, { target: { value: 'medium' } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(selector).toHaveValue('medium');
    installation = { status: 'running', model: 'tiny', message: 'Another installation started' };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(selector).toHaveValue('tiny');
    expect(selector).toBeDisabled();
  });
  it('retains an actionable error when installation is refused', async () => {
    const user = userEvent.setup(),
      fetcher = vi.fn((url: string) =>
        Promise.resolve(
          url === '/api/system/install'
            ? response({ error: 'Not enough disk space.' }, 422)
            : response(stoppedSystem),
        ),
      );
    vi.stubGlobal('fetch', fetcher);
    render(<SystemPanel {...props()} />);
    await user.click(await screen.findByRole('button', { name: 'Download & install' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Not enough disk space.');
    expect(screen.getByRole('button', { name: 'Download & install' })).toBeEnabled();
  });
});
