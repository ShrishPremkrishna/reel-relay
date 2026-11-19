import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Login } from './App';
import { response } from './test/fixtures';

describe('private workspace sign-in', () => {
  it('exchanges a token only in a JSON body and clears the input after success', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    const onSuccess = vi.fn().mockResolvedValue(undefined);
    render(<Login onSuccess={onSuccess} />);
    const input = screen.getByLabelText('Server access token');
    await user.type(input, 'private-test-token');
    await user.click(screen.getByRole('button', { name: 'Open workspace' }));
    expect(fetcher).toHaveBeenCalledWith(
      '/api/login',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ token: 'private-test-token' }),
      }),
    );
    expect(input).toHaveValue('');
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(location.search).toBe('');
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
  it('keeps the sign-in screen on an invalid token', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(response({ error: 'Invalid server access token' }, 401)),
    );
    const onSuccess = vi.fn();
    render(<Login onSuccess={onSuccess} />);
    await user.type(screen.getByLabelText('Server access token'), 'incorrect');
    await user.click(screen.getByRole('button', { name: 'Open workspace' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid server access token');
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
