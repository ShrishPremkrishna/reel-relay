import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MediaIntake, validateMediaFile } from './MediaIntake';
import { response } from '../test/fixtures';
const props = () => ({
  csrf: 'csrf-test',
  onComplete: vi.fn().mockResolvedValue(undefined),
  notify: vi.fn(),
});
describe('browser media intake', () => {
  it('uploads a video body with encoded metadata and the CSRF header', async () => {
    const user = userEvent.setup(),
      p = props(),
      fetcher = vi.fn().mockResolvedValue(response({ id: 'new-clip' }));
    vi.stubGlobal('fetch', fetcher);
    render(<MediaIntake {...p} mode="clip" />);
    await user.click(screen.getByRole('button', { name: 'Import video' }));
    const file = new File(['media'], 'moment & reaction.mp4', { type: 'video/mp4' });
    await user.upload(screen.getByLabelText('Source video file'), file);
    await user.type(screen.getByRole('textbox', { name: 'Source name' }), 'testcreator');
    await user.type(
      screen.getByRole('textbox', { name: 'Attribution' }),
      'Source: creator & friends',
    );
    await user.click(screen.getByRole('button', { name: 'Import into queue' }));
    await waitFor(() => expect(p.onComplete).toHaveBeenCalledWith('new-clip'));
    const [path, options] = fetcher.mock.calls[0];
    expect(new URL(path, 'http://localhost').searchParams.get('filename')).toBe(file.name);
    expect(new URL(path, 'http://localhost').searchParams.get('attribution')).toBe(
      'Source: creator & friends',
    );
    expect(options).toMatchObject({
      body: file,
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Relay-Token': 'csrf-test', 'Content-Type': 'application/octet-stream' },
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('keeps the file and metadata on upload rejection and permits an explicit corrected retry', async () => {
    const user = userEvent.setup(),
      p = props(),
      fetcher = vi.fn().mockResolvedValue(response({ error: 'No audio track was found.' }, 422));
    vi.stubGlobal('fetch', fetcher);
    render(<MediaIntake {...p} mode="asset" />);
    await user.click(screen.getByRole('button', { name: 'Add an effect' }));
    await user.upload(
      screen.getByLabelText('Effect file'),
      new File(['media'], 'impact.wav', { type: 'audio/wav' }),
    );
    await user.type(screen.getByRole('textbox', { name: 'Usage rights' }), 'Original recording');
    await user.type(screen.getByRole('textbox', { name: 'Mood & tags' }), 'impact, surprise');
    await user.click(screen.getByRole('button', { name: 'Add to library' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('No audio track');
    expect(screen.getByRole('textbox', { name: 'Usage rights' })).toHaveValue('Original recording');
    expect(screen.getByRole('button', { name: 'Add to library' })).toBeEnabled();
    expect(p.onComplete).not.toHaveBeenCalled();
    expect(new URL(fetcher.mock.calls[0][0], 'http://localhost').searchParams.get('rights')).toBe(
      'Original recording',
    );
  });
  it('rejects script/archive and oversized media before uploading', async () => {
    const p = props(),
      fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const user = userEvent.setup({ applyAccept: false });
    render(<MediaIntake {...p} mode="clip" />);
    await user.click(screen.getByRole('button', { name: 'Import video' }));
    await user.upload(
      screen.getByLabelText('Source video file'),
      new File(['payload'], 'archive.zip'),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('MP4, MOV, MKV or WebM');
    const file = new File(['media'], 'large.mp4');
    Object.defineProperty(file, 'size', { value: 100 * 1024 * 1024 + 1 });
    await user.upload(screen.getByLabelText('Source video file'), file);
    expect(await screen.findByRole('alert')).toHaveTextContent('100 MB');
    expect(screen.getByRole('button', { name: 'Import into queue' })).toBeDisabled();
    expect(fetcher).not.toHaveBeenCalled();
    expect(validateMediaFile(new File(['script'], 'effect.svg'), 'asset', 'meme')).toMatch(
      /supported image/,
    );
  });
  it('supports local image effects and closes without a mutation', async () => {
    const user = userEvent.setup(),
      fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    render(<MediaIntake {...props()} mode="asset" />);
    const trigger = screen.getByRole('button', { name: 'Add an effect' });
    await user.click(trigger);
    await user.selectOptions(screen.getByRole('combobox', { name: 'Effect type' }), 'meme');
    await user.upload(
      screen.getByLabelText('Effect file'),
      new File(['image'], 'reaction.png', { type: 'image/png' }),
    );
    expect(screen.getByRole('textbox', { name: 'Effect title' })).toHaveValue('reaction');
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { cancelable: true }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(fetcher).not.toHaveBeenCalled();
  });
});
