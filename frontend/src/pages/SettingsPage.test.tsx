import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';
import { setup, readiness, response } from '../test/fixtures';

const callbacks = () => ({ onChange: vi.fn().mockResolvedValue(undefined), notify: vi.fn() });
describe('setup and account safety', () => {
  it('resolves delivery readiness to concrete HTTPS configuration guidance', () => {
    render(
      <SettingsPage
        {...callbacks()}
        setup={setup}
        readiness={{
          ...readiness,
          checks: [
            {
              id: 'delivery',
              label: 'Video delivery',
              state: 'missing',
              detail: 'HTTPS needed',
              action: { label: 'Open setup', href: '/setup' },
            },
          ],
        }}
      />,
    );
    expect(screen.getByRole('link', { name: 'Set up HTTPS delivery' })).toHaveAttribute(
      'href',
      '/setup#delivery',
    );
    expect(
      screen.getByRole('heading', { name: 'Video delivery to Instagram' }).closest('section'),
    ).toHaveAttribute('id', 'delivery');
    expect(screen.getByText(/localhost URL is only reachable/)).toBeVisible();
    expect(screen.getByText('REEL_RELAY_WEB_TOKEN')).toBeVisible();
    expect(screen.getByText(/Restart the workspace with the new environment/)).toBeVisible();
  });
  it('saves Steady volume using the API mode while retaining independent pacing', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    render(
      <SettingsPage
        {...callbacks()}
        setup={{
          ...setup,
          selection_mode: 'balanced',
          publishing: { ...setup.publishing, max_per_day: 72, min_interval_seconds: 600 },
        }}
        readiness={readiness}
      />,
    );
    await user.selectOptions(screen.getByRole('combobox', { name: 'Editing goal' }), 'throughput');
    expect(screen.getByText(/Accept clear, entertaining moments/)).toHaveTextContent(
      'Both goals keep caption, audio and export checks.',
    );
    expect(screen.getByRole('spinbutton', { name: 'Maximum posts per rolling day' })).toHaveValue(
      72,
    );
    expect(screen.getByRole('spinbutton', { name: 'Minimum minutes between posts' })).toHaveValue(
      10,
    );
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
      selection_mode: 'throughput',
      max_per_day: 72,
      min_interval_seconds: 600,
    });
  });
  it('keeps an unsaved editing goal when a background snapshot arrives', async () => {
    const user = userEvent.setup();
    const props = callbacks();
    const { rerender } = render(
      <SettingsPage
        {...props}
        setup={{ ...setup, selection_mode: 'throughput' }}
        readiness={readiness}
      />,
    );
    const goal = screen.getByRole('combobox', { name: 'Editing goal' });
    await user.selectOptions(goal, 'balanced');
    rerender(
      <SettingsPage
        {...props}
        setup={{ ...setup, selection_mode: 'throughput' }}
        readiness={readiness}
      />,
    );
    expect(goal).toHaveValue('balanced');
  });
  it('preserves unsaved inputs when the server polls', async () => {
    const user = userEvent.setup();
    const props = callbacks();
    const { rerender } = render(<SettingsPage {...props} setup={setup} readiness={readiness} />);
    const model = screen.getByRole('textbox', { name: 'Installed model name' });
    await user.clear(model);
    await user.type(model, 'my-other-model');
    rerender(
      <SettingsPage {...props} setup={{ ...setup, model: 'server-model' }} readiness={readiness} />,
    );
    expect(model).toHaveValue('my-other-model');
    expect(screen.getByText('You have unsaved changes.')).toBeVisible();
  });
  it('saves a strict complete setup payload with numeric pacing and no display helper fields', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    render(<SettingsPage {...callbacks()} setup={setup} readiness={readiness} />);
    const interval = screen.getByRole('spinbutton', { name: 'Minimum minutes between posts' });
    await user.clear(interval);
    await user.type(interval, '17.5');
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const body = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(body).toEqual({
      channels: setup.channels,
      provider: 'ollama',
      model: setup.model,
      ...setup.auth,
      instagram_app_secret: null,
      max_per_day: 12,
      min_interval_seconds: 1050,
    });
    expect(body).not.toHaveProperty('publishing');
    expect(body).not.toHaveProperty('min_interval_minutes');
  });
  it('holds public posting until readiness and the exact connected account match', async () => {
    const user = userEvent.setup();
    const props = callbacks();
    const { rerender } = render(<SettingsPage {...props} setup={setup} readiness={readiness} />);
    expect(screen.getByRole('button', { name: 'Enable public posting' })).toBeDisabled();
    const connected = {
      ...setup,
      media_host_configured: true,
      accounts: { ...setup.accounts, instagram: { connected: true, username: 'clip_account' } },
    };
    rerender(
      <SettingsPage {...props} setup={connected} readiness={{ ...readiness, can_publish: true }} />,
    );
    const target = screen.getByRole('textbox', { name: 'Intended Instagram username' });
    await user.type(target, 'wrong_account');
    expect(screen.getByRole('button', { name: 'Enable public posting' })).toBeDisabled();
    await user.clear(target);
    await user.type(target, '@clip_account');
    expect(screen.getByRole('button', { name: 'Enable public posting' })).toBeEnabled();
  });
  it('retains edited setup when the server rejects a save', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(response({ error: 'This model is unavailable.' }, 422)),
    );
    render(<SettingsPage {...callbacks()} setup={setup} readiness={readiness} />);
    const model = screen.getByRole('textbox', { name: 'Installed model name' });
    await user.clear(model);
    await user.type(model, 'unavailable-model');
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('This model is unavailable.');
    expect(model).toHaveValue('unavailable-model');
    expect(screen.getByText('You have unsaved changes.')).toBeVisible();
  });
  it('pauses active publishing without requiring account reconnection', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    render(
      <SettingsPage
        {...callbacks()}
        setup={{
          ...setup,
          publishing: {
            ...setup.publishing,
            enabled: true,
            dry_run: false,
            expected_username: 'clip_account',
          },
        }}
        readiness={readiness}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Pause publishing' }));
    expect(fetcher).toHaveBeenCalledWith(
      '/api/publishing',
      expect.objectContaining({
        body: JSON.stringify({
          enabled: false,
          expected_username: 'clip_account',
          auto_approve: false,
        }),
      }),
    );
  });
});
