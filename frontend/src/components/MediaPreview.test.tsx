import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MediaPreview } from './MediaPreview';

describe('media preview failure', () => {
  it('replaces a broken player with an honest retryable state', () => {
    render(<MediaPreview src="/media/clip/video" label="Edited clip" />);
    fireEvent.error(screen.getByLabelText('Edited clip'));
    expect(screen.getByText('Preview unavailable')).toBeVisible();
    expect(screen.getByLabelText('Edited clip')).not.toBeVisible();
    expect(screen.getByRole('button', { name: 'Retry preview' })).toBeEnabled();
  });
});
