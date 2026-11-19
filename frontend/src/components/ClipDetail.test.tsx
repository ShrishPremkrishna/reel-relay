import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ClipDetail } from './ClipDetail';
import { candidate, response } from '../test/fixtures';

function mount(overrides = {}) {
  return render(
    <ClipDetail
      candidate={{ ...candidate, ...overrides }}
      csrf="csrf"
      onChange={vi.fn().mockResolvedValue(undefined)}
      notify={vi.fn()}
    />,
  );
}

describe('clip review flow', () => {
  it.each([
    ['Approve clip', '/api/approve'],
    ['Pass over', '/api/reject'],
  ])('binds %s to the reviewed candidate version', async (label, path) => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    mount();
    await user.click(screen.getByRole('button', { name: label }));
    expect(fetcher).toHaveBeenCalledWith(
      path,
      expect.objectContaining({
        body: JSON.stringify({ id: candidate.id, version: candidate.updated_at }),
      }),
    );
  });
  it.each(['editorial_review', 'rejected'])(
    'reassesses a %s source without bypassing selection',
    async (status) => {
      const user = userEvent.setup();
      const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
      vi.stubGlobal('fetch', fetcher);
      mount({ status, has_export: false, has_evidence: true, can_request_changes: false });
      await user.click(screen.getByRole('button', { name: 'Reassess moment' }));
      expect(fetcher).toHaveBeenCalledWith(
        '/api/reassess',
        expect.objectContaining({
          body: JSON.stringify({ id: candidate.id, version: candidate.updated_at, notes: '' }),
        }),
      );
      expect(screen.queryByRole('button', { name: 'Approve clip' })).not.toBeInTheDocument();
    },
  );
  it('sends optional reassessment context and keeps it on conflict', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ error: 'Changed' }, 409));
    vi.stubGlobal('fetch', fetcher);
    mount({
      status: 'editorial_review',
      has_export: false,
      has_evidence: true,
      can_request_changes: false,
    });
    await user.click(screen.getByText('Add context (optional)'));
    const context = screen.getByRole('textbox', { name: 'Context for reassessment' });
    await user.type(context, 'The joke is the contrast with the earlier promise.');
    await user.click(screen.getByRole('button', { name: 'Reassess moment' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('changed while you were reviewing');
    expect(context).toHaveValue('The joke is the contrast with the earlier promise.');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('identifies source comparison and requires returning to the edit for attestation', async () => {
    const user = userEvent.setup();
    mount({ status: 'needs_human_review' });
    await user.click(screen.getByRole('button', { name: 'Compare with source' }));
    expect(screen.getByText('THE SOURCE')).toBeVisible();
    expect(screen.queryByText('Complete a preview check')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save preview check' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Return to edit for final check' }));
    expect(screen.getByText('THE EDIT')).toBeVisible();
    expect(screen.getByText('Complete a preview check')).toBeInTheDocument();
  });
  it('keeps revision notes when the export changed and does not retry', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ error: 'Changed' }, 409));
    vi.stubGlobal('fetch', fetcher);
    mount();
    await user.click(screen.getByText('Request changes'));
    const notes = screen.getByRole('textbox', { name: /What should change/ });
    await user.type(notes, 'Keep the pause before the reveal.');
    await user.click(screen.getByRole('button', { name: 'Send to editor' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('changed while you were reviewing');
    expect(notes).toHaveValue('Keep the pause before the reveal.');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
      id: candidate.id,
      version: candidate.updated_at,
      notes: 'Keep the pause before the reveal.',
    });
  });
  it('requeues a proven failed upload with its exact candidate version', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    mount({ status: 'publish_failed' });
    await user.click(screen.getByRole('button', { name: 'Retry failed upload' }));
    expect(fetcher).toHaveBeenCalledWith(
      '/api/retry-publication',
      expect.objectContaining({
        body: JSON.stringify({ id: candidate.id, version: candidate.updated_at }),
      }),
    );
  });
  it('shows a failed-closed upload retry error without repeating the request', async () => {
    const user = userEvent.setup();
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        response(
          { error: 'This attempt has no proven pre-send failure. Reconcile it first.' },
          400,
        ),
      );
    vi.stubGlobal('fetch', fetcher);
    mount({ status: 'publish_failed' });
    await user.click(screen.getByRole('button', { name: 'Retry failed upload' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('no proven pre-send failure');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each(['publish_unknown', 'publishing'])('never offers an upload retry for %s', (status) => {
    mount({ status });
    expect(screen.queryByRole('button', { name: 'Retry failed upload' })).not.toBeInTheDocument();
  });
  it('keeps failed edit retry compatible with its id-only server contract', async () => {
    const user = userEvent.setup();
    const fetcher = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetcher);
    mount({ status: 'failed' });
    await user.click(screen.getByRole('button', { name: 'Retry edit' }));
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ id: candidate.id });
  });
  it('resets watched/listened attestation when the export changes', async () => {
    const user = userEvent.setup();
    const props = { csrf: 'csrf', onChange: vi.fn().mockResolvedValue(undefined), notify: vi.fn() };
    const { rerender } = render(
      <ClipDetail {...props} candidate={{ ...candidate, status: 'needs_human_review' }} />,
    );
    await user.click(screen.getByText('Complete a preview check'));
    const watched = screen.getByRole('checkbox', { name: /captions, framing/ });
    await user.click(watched);
    expect(watched).toBeChecked();
    rerender(
      <ClipDetail
        {...props}
        candidate={{ ...candidate, status: 'needs_human_review', export_version: 'new-export' }}
      />,
    );
    await user.click(screen.getByText('Complete a preview check'));
    expect(screen.getByRole('checkbox', { name: /captions, framing/ })).not.toBeChecked();
  });
  it('does not offer publishing approval for synthetic footage or uncertain posts', () => {
    const { rerender } = mount({ is_test: true });
    expect(screen.queryByRole('button', { name: 'Approve clip' })).not.toBeInTheDocument();
    rerender(
      <ClipDetail
        candidate={{ ...candidate, status: 'publish_unknown' }}
        csrf="csrf"
        onChange={vi.fn()}
        notify={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Instagram may have received/)).toBeVisible();
  });
  it('compares the real source with the export without reloading on status updates', async () => {
    const user = userEvent.setup();
    const props = { csrf: 'csrf', onChange: vi.fn(), notify: vi.fn() };
    const { rerender } = render(<ClipDetail {...props} candidate={candidate} />);
    const edited = screen.getByLabelText('testcreator edited clip');
    expect(edited).toHaveAttribute('src', `/media/${candidate.id}/video?v=export-1`);
    rerender(
      <ClipDetail
        {...props}
        candidate={{ ...candidate, updated_at: candidate.updated_at + 1, status: 'approved' }}
      />,
    );
    expect(screen.getByLabelText('testcreator edited clip')).toBe(edited);
    await user.click(screen.getByRole('button', { name: 'Compare with source' }));
    await waitFor(() =>
      expect(screen.getByLabelText('testcreator source moment')).toHaveAttribute(
        'src',
        `/media/${candidate.id}/source`,
      ),
    );
  });
});
