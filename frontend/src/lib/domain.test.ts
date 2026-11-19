import { describe, expect, it } from 'vitest';
import { candidateTitle, channelStatus, duration, isRecording } from './domain';
import { candidate } from '../test/fixtures';

describe('honest queue summaries', () => {
  it('does not call stale channel telemetry live', () => {
    const stale = {
      channel: 'test',
      capture_state: 'recording',
      updated_at: Date.now() / 1000 - 60,
    };
    expect(isRecording(stale)).toBe(false);
    expect(channelStatus(stale)).toMatchObject({ label: 'No recent update', tone: 'warning' });
  });
  it('keeps long rejection reasoning out of card headings', () => {
    expect(
      candidateTitle({
        ...candidate,
        caption: null,
        decision: { reason: 'Reason '.repeat(100) },
        start: 2,
        end: 40,
      }),
    ).toBe('Source moment · 38s');
  });
  it('preserves short effect timing and does not turn it into zero', () => {
    expect(duration(0.2)).toBe('0.2s');
    expect(duration(0.002)).toBe('<0.01s');
    expect(duration(73)).toBe('1m 13s');
  });
});
