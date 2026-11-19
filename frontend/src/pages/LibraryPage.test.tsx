import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { LibraryPage } from './LibraryPage';
import { response } from '../test/fixtures';

const items = Array.from({ length: 30 }, (_, i) => ({
  id: String(i),
  title: `Sound ${i}`,
  kind: 'sfx',
  tags: ['surprise'],
  duration: 0.2,
  rights: 'Original local sound',
  attribution: 'Reel Relay',
  preview_url: '/api/assets/' + i + '/preview',
}));
describe('effects library', () => {
  it('bounds media elements, loads more and resets the search page', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ items, total: 30, warnings: [] })));
    render(<LibraryPage />);
    await screen.findByText('Sound 0');
    expect(screen.getAllByRole('article')).toHaveLength(24);
    expect(screen.getAllByText('0.2s')).toHaveLength(24);
    await user.click(screen.getByRole('button', { name: 'Load more effects' }));
    expect(screen.getAllByRole('article')).toHaveLength(30);
    await user.type(screen.getByRole('searchbox', { name: 'Search effects' }), 'Sound 29');
    expect(screen.getAllByRole('article')).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Load more effects' })).not.toBeInTheDocument();
    await user.clear(screen.getByRole('searchbox', { name: 'Search effects' }));
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(24));
  });

  it('renders image assets as images when the server reports their media type', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response({
          items: [{ ...items[0], title: 'Reaction still', kind: 'meme', media_type: 'image/png' }],
          total: 1,
          warnings: [],
        }),
      ),
    );
    render(<LibraryPage />);
    expect(await screen.findByRole('img', { name: 'Reaction still' })).toHaveAttribute(
      'src',
      '/api/assets/0/preview',
    );
    expect(screen.queryByLabelText('Preview Reaction still')).not.toBeInTheDocument();
  });
  it('shows a retryable API error without inventing a library', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<LibraryPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach your workspace');
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled();
  });
  it('does not embed off-origin preview URLs', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response({
          items: [{ ...items[0], preview_url: '//elsewhere.example/track?secret=x' }],
          total: 1,
          warnings: [],
        }),
      ),
    );
    render(<LibraryPage />);
    await screen.findByText('Sound 0');
    expect(screen.queryByLabelText('Listen to Sound 0')).not.toBeInTheDocument();
    expect(screen.getByText('No preview available')).toBeVisible();
  });
});
