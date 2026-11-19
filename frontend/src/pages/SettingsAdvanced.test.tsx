import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';
import { readiness, response } from '../test/fixtures';
import { fullSetup, stoppedSystem } from '../test/settingsFixtures';
const callbacks = () => ({ onChange: vi.fn().mockResolvedValue(undefined), notify: vi.fn() });
function mockServer(save = () => response({ ok: true })) {
  const fn = vi.fn((path: string) =>
    Promise.resolve(path === '/api/system' ? response(stoppedSystem) : save()),
  );
  vi.stubGlobal('fetch', fn);
  return fn;
}
function writes(fetcher: ReturnType<typeof mockServer>) {
  return fetcher.mock.calls.filter((c) => c[0] === '/api/setup') as unknown as [
    string,
    RequestInit,
  ][];
}
async function open(user: ReturnType<typeof userEvent.setup>, text: string) {
  await user.click(screen.getByText(text, { selector: 'summary' }));
}
describe('complete browser configuration', () => {
  it('saves nested style, detector and content choices with the original config version', async () => {
    const user = userEvent.setup(),
      fetcher = mockServer();
    const props = callbacks();
    const view = render(<SettingsPage {...props} setup={fullSetup} readiness={readiness} />);
    await user.selectOptions(screen.getByRole('combobox', { name: 'Default framing' }), 'contain');
    await user.click(screen.getByRole('checkbox', { name: 'Burn in speech captions' }));
    await open(user, 'Content you allow');
    await user.click(screen.getByRole('checkbox', { name: 'Allow Third-party music' }));
    expect(screen.getByText(/Unrecognized flags stay held/)).toHaveTextContent('unknown_old_flag');
    expect(screen.queryByRole('checkbox', { name: /Allow unknown/ })).not.toBeInTheDocument();
    await open(user, 'Chat spike sensitivity');
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Activity multiplier' }), {
      target: { value: '2.5' },
    });
    view.rerender(
      <SettingsPage
        {...props}
        setup={{ ...fullSetup, config_version: 'version-two' }}
        readiness={readiness}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(writes(fetcher)).toHaveLength(1));
    const body = JSON.parse(String(writes(fetcher)[0][1].body));
    expect(body).toMatchObject({
      config_version: 'version-one',
      editing: { layout: 'contain', captions_enabled: false, caption_words: 3 },
      detector: { ratio: 2.5 },
      agent_controls: { allowed_flags: ['strong_language', 'unknown_old_flag', 'music'] },
    });
    expect(writes(fetcher)[0][1].headers).toMatchObject({ 'X-Relay-Token': 'csrf-test' });
  });
  it('keeps rejected stale edits and only discards on an explicit reload', async () => {
    const user = userEvent.setup(),
      fetcher = mockServer(() =>
        response({ error: 'Settings changed. Reload before saving.' }, 409),
      ),
      props = callbacks();
    const view = render(<SettingsPage {...props} setup={fullSetup} readiness={readiness} />);
    await user.type(screen.getByRole('textbox', { name: 'Style notes' }), 'Keep pauses.');
    view.rerender(
      <SettingsPage
        {...props}
        setup={{
          ...fullSetup,
          config_version: 'version-two',
          editing: { ...fullSetup.editing!, style_notes: 'Server notes' },
        }}
        readiness={readiness}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Your changes are still here');
    expect(screen.getByRole('textbox', { name: 'Style notes' })).toHaveValue('Keep pauses.');
    expect(writes(fetcher)).toHaveLength(1);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Discard changes & reload' })).toBeEnabled(),
    );
    await user.click(screen.getByRole('button', { name: 'Discard changes & reload' }));
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Style notes' })).toHaveValue('Server notes'),
    );
  });

  it('preserves a saved draft when the confirming refresh fails', async () => {
    const user = userEvent.setup();
    mockServer();
    const p = callbacks();
    p.onChange.mockRejectedValue(new Error('Connection interrupted during refresh.'));
    render(<SettingsPage {...p} setup={fullSetup} readiness={readiness} />);
    await user.type(screen.getByRole('textbox', { name: 'Style notes' }), 'Keep the pause');
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Connection interrupted during refresh.',
    );
    expect(screen.getByRole('textbox', { name: 'Style notes' })).toHaveValue('Keep the pause');
    expect(screen.getByText('You have unsaved changes.')).toBeVisible();
  });
  it('rejects contradictory lengths and incomplete paired storage credentials before sending', async () => {
    const user = userEvent.setup(),
      fetcher = mockServer();
    render(<SettingsPage {...callbacks()} setup={fullSetup} readiness={readiness} />);
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Minimum clip length (seconds)' }), {
      target: { value: '70' },
    });
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Minimum clip length must not exceed',
    );
    expect(writes(fetcher)).toHaveLength(0);
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Minimum clip length (seconds)' }), {
      target: { value: '5' },
    });
    await open(user, 'Use S3-compatible storage');
    await user.type(screen.getByLabelText('Storage access key ID'), 'example-key');
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter both storage keys');
    expect(writes(fetcher)).toHaveLength(0);
  });
  it('uses one Gemini key with separate editorial and media permissions', async () => {
    const user = userEvent.setup(),
      fetcher = mockServer();
    render(<SettingsPage {...callbacks()} setup={fullSetup} readiness={readiness} />);
    await user.selectOptions(screen.getByRole('combobox', { name: 'Editing provider' }), 'gemini');
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Media perception provider' }),
      'gemini',
    );
    expect(screen.getAllByLabelText('Gemini API key')).toHaveLength(1);
    await user.type(screen.getByLabelText('Gemini API key'), 'example-api-key');
    await user.click(screen.getByRole('checkbox', { name: 'Allow Gemini editorial uploads' }));
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(writes(fetcher)).toHaveLength(1));
    expect(JSON.parse(String(writes(fetcher)[0][1].body))).toMatchObject({
      provider: 'gemini',
      gemini_api_key: 'example-api-key',
      agent_controls: { allow_cloud_upload: true },
      allow_cloud_upload: false,
    });
    expect(screen.queryByDisplayValue('example-api-key')).not.toBeInTheDocument();
  });
  it('identifies environment-managed credentials and disables replacing or removing them', async () => {
    const user = userEvent.setup();
    mockServer();
    render(
      <SettingsPage
        {...callbacks()}
        setup={{
          ...fullSetup,
          perception: { ...fullSetup.perception!, provider: 'gemini', api_key_saved: true },
          s3_credentials_saved: true,
          credential_sources: {
            ...fullSetup.credential_sources!,
            gemini: 'environment',
            s3: 'environment',
          },
        }}
        readiness={readiness}
      />,
    );
    expect(screen.getByLabelText('Gemini API key')).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Remove saved Gemini API key' })).toBeDisabled();
    await open(user, 'Use S3-compatible storage');
    expect(screen.getByLabelText('Storage access key ID')).toBeDisabled();
    expect(
      screen.getByRole('checkbox', { name: 'Remove saved storage credentials' }),
    ).toBeDisabled();
  });
  it('sends explicit saved-key removal and displays restart requirements', async () => {
    const user = userEvent.setup(),
      fetcher = mockServer(() =>
        response({
          ok: true,
          restart_required: true,
          message: 'Restart to apply the new HTTPS origin.',
        }),
      );
    render(<SettingsPage {...callbacks()} setup={fullSetup} readiness={readiness} />);
    await open(user, 'Meta app configuration');
    await user.click(screen.getByRole('checkbox', { name: 'Remove saved Instagram app secret' }));
    await user.type(
      screen.getByRole('textbox', { name: 'Public workspace HTTPS origin' }),
      'https://relay.example.com',
    );
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(writes(fetcher)).toHaveLength(1));
    expect(JSON.parse(String(writes(fetcher)[0][1].body))).toMatchObject({
      clear_instagram_app_secret: true,
      instagram_app_secret: null,
      delivery: { public_base_url: 'https://relay.example.com' },
    });
    expect(await screen.findByText('Restart to apply the new HTTPS origin.')).toBeVisible();
  });
  it('opens advanced details for an invalid number and never saves a null numeric value', async () => {
    const user = userEvent.setup(),
      fetcher = mockServer();
    render(<SettingsPage {...callbacks()} setup={fullSetup} readiness={readiness} />);
    await open(user, 'Caption & effect limits');
    await user.clear(screen.getByRole('spinbutton', { name: 'Words per caption' }));
    await open(user, 'Caption & effect limits');
    await user.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(screen.getByRole('spinbutton', { name: 'Words per caption' })).toBeVisible();
    expect(writes(fetcher)).toHaveLength(0);
  });
});
