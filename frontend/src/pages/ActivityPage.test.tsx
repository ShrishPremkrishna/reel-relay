import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ActivityPage } from './ActivityPage';
import { candidate, response } from '../test/fixtures';

const latest = {
  id: 50,
  at: 1700000000,
  kind: 'candidate_reviewed',
  candidate_id: candidate.id,
  summary: 'Review completed.',
  level: 'info',
};
describe('workspace activity', () => {
  it('loads earlier events with a bounded cursor and no duplicate rows', async () => {
    const user = userEvent.setup();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response({ items: [latest], next_before: 50 }))
      .mockResolvedValueOnce(
        response({
          items: [latest, { ...latest, id: 49, summary: 'Source captured.' }],
          next_before: null,
        }),
      );
    vi.stubGlobal('fetch', fetcher);
    render(<ActivityPage />);
    await screen.findByText('Review completed.');
    await user.click(screen.getByRole('button', { name: 'Load earlier activity' }));
    expect(await screen.findByText('Source captured.')).toBeVisible();
    expect(screen.getAllByText('Review completed.')).toHaveLength(1);
    expect(fetcher.mock.calls[1][0]).toBe('/api/activity?limit=30&before=50');
    expect(screen.getAllByRole('link', { name: /Open clip/ })[0]).toHaveAttribute(
      'href',
      `/?clip=${candidate.id}`,
    );
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Load earlier activity' }),
      ).not.toBeInTheDocument(),
    );
  });
  it('aborts an outstanding read when leaving the page', async () => {
    let signal: AbortSignal | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((_path, options) => {
        signal = options.signal;
        return new Promise(() => {});
      }),
    );
    const { unmount } = render(<ActivityPage />);
    expect(signal?.aborted).toBe(false);
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});
