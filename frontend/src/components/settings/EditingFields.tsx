import { SlidersHorizontal } from 'lucide-react';
import type { EditingSettings } from '../../lib/settingsSchema';
import { Field, Section } from '../primitives';
import { Advanced, NumberField, Toggle } from './fields';

export function EditingFields({
  value,
  onChange,
}: {
  value: EditingSettings;
  onChange: (value: EditingSettings) => void;
}) {
  return (
    <Section
      id="style"
      title="Editing style"
      description="Set a repeatable house style. The editor still chooses cuts and effects that fit each moment."
      action={<SlidersHorizontal size={21} className="section-icon" />}
    >
      <div className="form-grid">
        <NumberField
          label="Minimum clip length (seconds)"
          value={value.min_seconds}
          min={5}
          max={90}
          onChange={(v) => onChange({ ...value, min_seconds: v })}
        />
        <NumberField
          label="Maximum clip length (seconds)"
          value={value.max_seconds}
          min={5}
          max={90}
          onChange={(v) => onChange({ ...value, max_seconds: v })}
        />
      </div>
      <Field label="Default framing">
        <select
          value={value.layout}
          onChange={(e) =>
            onChange({ ...value, layout: e.target.value as EditingSettings['layout'] })
          }
        >
          <option value="auto">Let the editor choose</option>
          <option value="contain">Keep the whole frame</option>
          <option value="focus">Focus on the action</option>
          <option value="split">Split reaction and gameplay</option>
        </select>
      </Field>
      <div className="settings-toggle-grid">
        <Toggle
          label="Burn in speech captions"
          description="Speech subtitles on the final video. Word timing is still checked when captions are off."
          checked={value.captions_enabled}
          onChange={(v) => onChange({ ...value, captions_enabled: v })}
        />
        <Toggle
          label="Use sound effects"
          description="Choose from your actual effects library."
          checked={value.sfx_enabled}
          onChange={(v) => onChange({ ...value, sfx_enabled: v })}
        />
        <Toggle
          label="Use visual overlays"
          description="Allow catalogued reactions and visual inserts."
          checked={value.overlays_enabled}
          onChange={(v) => onChange({ ...value, overlays_enabled: v })}
        />
      </div>
      <Field
        label="Style notes"
        hint="Persistent direction for the editor. Specific preferences work best."
      >
        <textarea
          value={value.style_notes}
          onChange={(e) => onChange({ ...value, style_notes: e.target.value })}
          rows={3}
          maxLength={2000}
          placeholder="Keep comic pauses. Use simple captions. Prefer a clean edit over extra effects."
        />
      </Field>
      <Advanced title="Caption & effect limits">
        <div className="form-grid">
          <NumberField
            label="Words per caption"
            value={value.caption_words}
            min={1}
            max={6}
            disabled={!value.captions_enabled}
            onChange={(v) => onChange({ ...value, caption_words: v })}
          />
          <NumberField
            label="Maximum sound effects"
            value={value.max_sfx}
            min={0}
            max={12}
            disabled={!value.sfx_enabled}
            onChange={(v) => onChange({ ...value, max_sfx: v })}
          />
          <NumberField
            label="Maximum visual overlays"
            value={value.max_overlays}
            min={0}
            max={8}
            disabled={!value.overlays_enabled}
            onChange={(v) => onChange({ ...value, max_overlays: v })}
          />
          <NumberField
            label="Sound effect gain (dB)"
            value={value.sfx_gain_db}
            min={-36}
            max={-6}
            step={0.5}
            disabled={!value.sfx_enabled}
            onChange={(v) => onChange({ ...value, sfx_gain_db: v })}
          />
        </div>
      </Advanced>
    </Section>
  );
}
