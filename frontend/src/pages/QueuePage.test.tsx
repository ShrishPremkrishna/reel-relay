import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { QueuePage } from './QueuePage';
import { candidate, setup, readiness, workspace } from '../test/fixtures';
const props = {
  setup,
  readiness,
  refreshing: false,
  reload: vi.fn().mockResolvedValue(undefined),
  notify: vi.fn(),
};
describe('queue navigation', () => {
  it('opens a linked published clip in its correct status tab', () => {
    window.history.replaceState({}, '', `/?clip=${candidate.id}`);
    render(
      <QueuePage
        {...props}
        state={{
          ...workspace,
          candidates: [{ ...candidate, status: 'published' }],
          counts: { published: 1 },
        }}
      />,
    );
    expect(screen.getByRole('tab', { name: 'Published 1' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByLabelText('testcreator edited clip')).toBeInTheDocument();
  });
  it('does not show an unrelated clip for a historical link outside the snapshot', () => {
    window.history.replaceState({}, '', '/?clip=older-clip');
    render(
      <QueuePage
        {...props}
        state={{
          ...workspace,
          candidates: [{ ...candidate, status: 'needs_human_review' }],
          counts: { needs_human_review: 3 },
        }}
      />,
    );
    expect(screen.getByText('This clip is outside the current queue snapshot.')).toBeVisible();
    expect(screen.queryByLabelText('testcreator edited clip')).not.toBeInTheDocument();
  });
  it('moves through the tabs with keyboard arrows and clears a filtered-out preview', async () => {
    const user = userEvent.setup();
    render(<QueuePage {...props} state={workspace} />);
    const review = screen.getByRole('tab', { name: 'Needs review 0' });
    review.focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Ready 1' })).toHaveFocus();
    expect(screen.getByLabelText('testcreator edited clip')).toBeInTheDocument();
    await user.type(screen.getByRole('searchbox', { name: 'Search clips' }), 'no such moment');
    expect(screen.getByText('No matching moments')).toBeVisible();
    expect(screen.queryByLabelText('testcreator edited clip')).not.toBeInTheDocument();
  });
  it('moves focus and scrolls to the chosen preview in the stacked mobile layout', async () => {
    const user = userEvent.setup();
    const scroll = vi.fn();
    vi.stubGlobal(
      'matchMedia',
      vi.fn((query: string) => ({ matches: query.includes('max-width'), media: query })),
    );
    const original = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scroll;
    try {
      window.history.replaceState({}, '', `/?clip=${candidate.id}`);
      render(<QueuePage {...props} state={workspace} />);
      await user.click(screen.getByRole('button', { name: /EDIT testcreator/ }));
      expect(screen.getByLabelText('Selected clip preview')).toHaveFocus();
      expect(scroll).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
    } finally {
      HTMLElement.prototype.scrollIntoView = original;
    }
  });
  it('uses the full queue counts, while cards show only the bounded latest snapshot', () => {
    render(<QueuePage {...props} state={{ ...workspace, counts: { ready: 25, failed: 4 } }} />);
    expect(screen.getByRole('tab', { name: 'Ready 25' })).toBeVisible();
    expect(screen.getByText(/Showing the latest 1 moments/)).toBeVisible();
  });
});
