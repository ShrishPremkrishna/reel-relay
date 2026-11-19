import type { CaptureSettings, DetectorSettings } from '../../lib/settingsSchema';
import { Field } from '../primitives';
import { Advanced, NumberField, StringListField } from './fields';

export function CaptureFields({
  capture,
  detector,
  onCapture,
  onDetector,
}: {
  capture?: CaptureSettings;
  detector?: DetectorSettings;
  onCapture: (value: CaptureSettings) => void;
  onDetector: (value: DetectorSettings) => void;
}) {
  return (
    <>
      {capture && (
        <Advanced
          title="Capture window & capacity"
          description="Keep enough context around a chat spike. The rolling buffer must cover the before/after window plus 30 seconds."
        >
          <div className="form-grid">
            <NumberField
              label="Seconds before a spike"
              value={capture.pre_seconds}
              min={0}
              max={120}
              onChange={(v) => onCapture({ ...capture, pre_seconds: v })}
            />
            <NumberField
              label="Seconds after a spike"
              value={capture.post_seconds}
              min={1}
              max={120}
              onChange={(v) => onCapture({ ...capture, post_seconds: v })}
            />
            <NumberField
              label="Rolling buffer (seconds)"
              value={capture.retention_seconds}
              min={60}
              max={3600}
              onChange={(v) => onCapture({ ...capture, retention_seconds: v })}
            />
            <NumberField
              label="Chat/video offset (seconds)"
              value={capture.chat_video_offset_seconds}
              min={-120}
              max={120}
              step={0.1}
              hint="Positive values move the selected moment earlier."
              onChange={(v) => onCapture({ ...capture, chat_video_offset_seconds: v })}
            />
          </div>
          <Field
            label="Stream quality preference"
            hint="Comma-separated fallback order, such as 720p60,720p,best."
          >
            <input
              value={capture.quality}
              onChange={(e) => onCapture({ ...capture, quality: e.target.value })}
              required
            />
          </Field>
          <div className="form-grid">
            <NumberField
              label="Maximum active channels"
              value={capture.max_channels}
              min={1}
              max={64}
              onChange={(v) => onCapture({ ...capture, max_channels: v })}
            />
            <NumberField
              label="Pending moment limit"
              value={capture.max_pending}
              min={1}
              max={500}
              onChange={(v) => onCapture({ ...capture, max_pending: v })}
            />
            <NumberField
              label="Minimum free disk space (GB)"
              value={capture.min_free_gb}
              min={0.1}
              max={1000}
              step={0.1}
              onChange={(v) => onCapture({ ...capture, min_free_gb: v })}
            />
            <NumberField
              label="Reconnect delay (seconds)"
              value={capture.reconnect_seconds}
              min={1}
              max={300}
              onChange={(v) => onCapture({ ...capture, reconnect_seconds: v })}
            />
            <NumberField
              label="Recording segment (seconds)"
              value={capture.segment_seconds}
              min={1}
              max={15}
              onChange={(v) => onCapture({ ...capture, segment_seconds: v })}
            />
          </div>
        </Advanced>
      )}
      {detector && (
        <Advanced
          title="Chat spike sensitivity"
          description="Tune how much activity deserves a closer look. Lower thresholds find more candidates and use more editing capacity."
        >
          <div className="form-grid">
            <NumberField
              label="Activity multiplier"
              value={detector.ratio}
              min={1.2}
              max={20}
              step={0.1}
              onChange={(v) => onDetector({ ...detector, ratio: v })}
            />
            <NumberField
              label="Minimum messages"
              value={detector.min_messages}
              min={2}
              max={100000}
              onChange={(v) => onDetector({ ...detector, min_messages: v })}
            />
            <NumberField
              label="Minimum unique viewers"
              value={detector.min_users}
              min={2}
              max={100000}
              onChange={(v) => onDetector({ ...detector, min_users: v })}
            />
            <NumberField
              label="Messages per viewer counted"
              value={detector.per_user_cap}
              min={1}
              max={20}
              onChange={(v) => onDetector({ ...detector, per_user_cap: v })}
            />
            <NumberField
              label="Cooldown between moments (seconds)"
              value={detector.cooldown_seconds}
              min={5}
              max={3600}
              onChange={(v) => onDetector({ ...detector, cooldown_seconds: v })}
            />
            <NumberField
              label="Activity window (seconds)"
              value={detector.window_seconds}
              min={2}
              max={60}
              onChange={(v) => onDetector({ ...detector, window_seconds: v })}
            />
            <NumberField
              label="Baseline windows"
              value={detector.baseline_windows}
              min={2}
              max={120}
              onChange={(v) => onDetector({ ...detector, baseline_windows: v })}
            />
            <NumberField
              label="Warm-up windows"
              value={detector.warmup_windows}
              min={2}
              max={120}
              onChange={(v) => onDetector({ ...detector, warmup_windows: v })}
            />
          </div>
          <StringListField
            label="Ignored chat usernames"
            value={detector.ignored_users}
            onChange={(v) => onDetector({ ...detector, ignored_users: v })}
            hint="Separate names with commas or new lines."
          />
        </Advanced>
      )}
    </>
  );
}
