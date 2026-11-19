import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from '../pages/SettingsPage';
import { setup, readiness, response } from '../test/fixtures';

const configured = {
  ...setup,
  perception: {
    provider: 'none' as const,
    model: '',
    allow_cloud_upload: false,
    api_key_saved: false,
  },
};
describe('media perception setup', () => {
  it('explains local volume checks without requiring an optional media model', async () => {
    const user = userEvent.setup();
    render(
      <SettingsPage
        setup={{ ...configured, selection_mode: 'throughput' }}
        readiness={{
          ...readiness,
          perception: {
            provider: 'local-asr',
            modalities: ['audio', 'captions'],
            configured: true,
          },
        }}
        onChange={vi.fn()}
        notify={vi.fn()}
      />,
    );
    expect(screen.getByRole('heading', { name: 'Additional media review' })).toBeVisible();
    expect(screen.getByText(/Steady volume uses automatic local checks/)).toHaveTextContent(
      'The optional model below is used with More selective.',
    );
    expect(screen.getByText(/No additional media model is configured/)).toBeVisible();
    expect(
      screen.queryByText(/Exports requiring those checks remain held/),
    ).not.toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Editing goal' }), 'balanced');
    expect(screen.getByRole('heading', { name: 'Sound & motion review' })).toBeVisible();
    expect(screen.getByText(/Exports requiring those checks remain held/)).toBeVisible();
  });
  it('allows automatic mode when local ASR checks are ready without a native-audio model', () => {
    render(
      <SettingsPage
        setup={{
          ...configured,
          selection_mode: 'throughput',
          media_host_configured: true,
          accounts: { ...setup.accounts, instagram: { connected: true, username: 'clip_account' } },
        }}
        readiness={{
          ...readiness,
          can_publish: true,
          perception: {
            provider: 'local-asr',
            modalities: ['audio', 'captions'],
            configured: true,
          },
        }}
        onChange={vi.fn()}
        notify={vi.fn()}
      />,
    );
    expect(screen.getByRole('combobox', { name: 'Media perception provider' })).toHaveValue('none');
    expect(screen.getByRole('checkbox', { name: /Publish automatically/ })).toBeEnabled();
  });
  it('keeps local perception distinct from the editorial model and needs no API key', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    render(
      <SettingsPage
        setup={configured}
        readiness={readiness}
        onChange={vi.fn().mockResolvedValue(undefined)}
        notify={vi.fn()}
      />,
    );
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Media perception provider' }),
      'ollama',
    );
    await user.type(
      screen.getByRole('textbox', { name: 'Perception model (optional)' }),
      'audio-video-model',
    );
    expect(screen.queryByLabelText(/^Gemini API key/)).not.toBeInTheDocument();
    expect(screen.getByText(/No cloud API key is needed/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    const body = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(body).toMatchObject({
      provider: 'ollama',
      model: setup.model,
      perception_provider: 'ollama',
      perception_model: 'audio-video-model',
      allow_cloud_upload: false,
      gemini_api_key: null,
    });
  });
  it('keeps cloud upload opt-in explicit and sends a key only in the POST body', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    render(
      <SettingsPage
        setup={configured}
        readiness={readiness}
        onChange={vi.fn().mockResolvedValue(undefined)}
        notify={vi.fn()}
      />,
    );
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Media perception provider' }),
      'gemini',
    );
    const consent = screen.getByRole('checkbox', { name: /allow source clips and audio/ });
    expect(consent).not.toBeChecked();
    expect(screen.getByText('Key needed')).toBeVisible();
    const key = screen.getByLabelText(/^Gemini API key/);
    await user.type(key, 'test-private-key');
    expect(consent).not.toBeChecked();
    await user.click(consent);
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(fetcher.mock.calls[0][0]).toBe('/api/setup');
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({
      allow_cloud_upload: true,
      gemini_api_key: 'test-private-key',
    });
    expect(key).toHaveValue('');
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
  it('disables automatic publishing while media review is unconfigured', () => {
    render(
      <SettingsPage
        setup={{
          ...configured,
          media_host_configured: true,
          accounts: { ...setup.accounts, instagram: { connected: true, username: 'clip_account' } },
        }}
        readiness={{ ...readiness, can_publish: true }}
        onChange={vi.fn()}
        notify={vi.fn()}
      />,
    );
    expect(screen.getByRole('checkbox', { name: /Publish automatically/ })).toBeDisabled();
  });
});
