import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SourcesPage } from './SourcesPage';
import { setup, workspace } from '../test/fixtures';
import { workspaceSchema } from '../lib/models';

describe('configured source health', () => {
  it('shows real editing backpressure and clears it when fresh intake resumes', () => {
    const fresh = {
      channel: 'testcreator',
      updated_at: Date.now() / 1000,
      capture_state: 'recording',
      processing_backlog: 20,
      processing_limit: 20,
      intake_state: 'processing_backlog_full' as const,
    };
    const state = workspaceSchema.parse({ ...workspace, channels: [fresh] });
    const { rerender } = render(<SourcesPage setup={setup} state={state} />);
    expect(screen.getByText('Recording')).toBeVisible();
    expect(screen.getByText('New clips paused while the editor catches up.')).toBeVisible();
    expect(
      screen.getByText('Editing queue: 20 of 20. Completed and held clips do not count.'),
    ).toBeVisible();
    expect(screen.getByText(/New clips resume automatically/)).toBeVisible();
    expect(screen.queryByText('./relay start')).not.toBeInTheDocument();
    rerender(
      <SourcesPage
        setup={setup}
        state={{
          ...state,
          channels: [{ ...fresh, processing_backlog: 19, intake_state: 'accepting' }],
        }}
      />,
    );
    expect(screen.queryByText(/New clips paused/)).not.toBeInTheDocument();
  });
  it('identifies pending capture windows separately from editing backpressure', () => {
    render(
      <SourcesPage
        setup={setup}
        state={{
          ...workspace,
          channels: [
            {
              channel: 'testcreator',
              updated_at: Date.now() / 1000,
              capture_state: 'recording',
              intake_state: 'pending_windows_full',
              pending_windows: 20,
              processing_backlog: 1,
              processing_limit: 20,
            },
          ],
        }}
      />,
    );
    expect(
      screen.getByText('New clips paused while pending capture windows finish.'),
    ).toBeVisible();
    expect(screen.getByText(/New clips resume automatically/)).toBeVisible();
    expect(screen.queryByText(/Editing queue:/)).not.toBeInTheDocument();
  });
  it('does not infer intake pressure from finished or held queue counts', () => {
    render(
      <SourcesPage
        setup={setup}
        state={{
          ...workspace,
          counts: { approved: 70, editorial_review: 30 },
          channels: [
            {
              channel: 'testcreator',
              updated_at: Date.now() / 1000,
              capture_state: 'recording',
              intake_state: 'accepting',
              processing_backlog: 2,
              processing_limit: 20,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText('Recording')).toBeVisible();
    expect(screen.queryByText(/New clips paused/)).not.toBeInTheDocument();
  });
  it('does not show a current intake pause from stale or disabled source telemetry', () => {
    const health = {
      channel: 'testcreator',
      updated_at: Date.now() / 1000 - 60,
      capture_state: 'recording',
      intake_state: 'processing_backlog_full' as const,
      processing_backlog: 20,
      processing_limit: 20,
    };
    const { rerender } = render(
      <SourcesPage setup={setup} state={{ ...workspace, channels: [health] }} />,
    );
    expect(screen.getByText('No recent update')).toBeVisible();
    expect(screen.queryByText(/New clips paused/)).not.toBeInTheDocument();
    rerender(
      <SourcesPage
        setup={{ ...setup, channels: setup.channels.map((c) => ({ ...c, enabled: false })) }}
        state={{ ...workspace, channels: [{ ...health, updated_at: Date.now() / 1000 }] }}
      />,
    );
    expect(screen.getByText('Not watching')).toBeVisible();
    expect(screen.queryByText(/New clips paused/)).not.toBeInTheDocument();
  });
  it('explains how to start capture when only the web interface is running', () => {
    render(
      <SourcesPage
        setup={setup}
        state={{
          ...workspace,
          channels: [
            { channel: 'testcreator', updated_at: Date.now() / 1000, capture_state: 'stopped' },
          ],
        }}
      />,
    );
    expect(screen.getByText('Configured to watch; capture is stopped.')).toBeVisible();
    expect(screen.getByText('./relay start')).toBeVisible();
    expect(screen.getByText('Configured to watch')).toBeVisible();
  });
  it('does not show startup instructions for a fresh active recording', () => {
    render(
      <SourcesPage
        setup={setup}
        state={{
          ...workspace,
          channels: [
            { channel: 'testcreator', updated_at: Date.now() / 1000, capture_state: 'recording' },
          ],
        }}
      />,
    );
    expect(screen.getByText('Recording')).toBeVisible();
    expect(screen.queryByText('./relay start')).not.toBeInTheDocument();
  });

  it('links stopped current workspaces to in-app service controls', () => {
    render(
      <SourcesPage
        setup={{ ...setup, config_version: 'version' }}
        state={{ ...workspace, channels: [] }}
      />,
    );
    expect(screen.getByRole('link', { name: 'Workspace services' })).toHaveAttribute(
      'href',
      '/setup#runtime',
    );
    expect(screen.queryByText('./relay start')).not.toBeInTheDocument();
  });
});
