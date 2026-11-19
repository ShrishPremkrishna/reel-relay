import type { MediaSettings, PerceptionControls } from '../../lib/settingsSchema';
import type { SetupDraft } from '../../lib/models';
import { Field } from '../primitives';
import { Advanced, CommandField, NumberField } from './fields';

export function MediaFields({
  media,
  perception,
  provider,
  onMedia,
  onPerception,
  onInvalidEdit,
}: {
  media?: MediaSettings;
  perception?: PerceptionControls;
  provider: SetupDraft['perception_provider'];
  onMedia: (value: MediaSettings) => void;
  onPerception: (value: PerceptionControls) => void;
  onInvalidEdit: () => void;
}) {
  return (
    <>
      {media && (
        <Advanced
          title="Local media & library paths"
          description="Transcription installation fills the runtime and model paths automatically. Use overrides only for an existing installation. Media paths must stay within the workspace data directory."
        >
          <Field label="Media engine">
            <select
              value={media.engine}
              onChange={(e) =>
                onMedia({ ...media, engine: e.target.value as MediaSettings['engine'] })
              }
            >
              <option value="builtin">Included standalone engine</option>
              <option value="reel-studio">Existing Reel Studio adapter</option>
            </select>
          </Field>
          <div className="form-grid">
            <Field label="Transcription language" hint="Leave blank for automatic detection.">
              <input
                value={media.transcription_language}
                maxLength={12}
                onChange={(e) => onMedia({ ...media, transcription_language: e.target.value })}
                placeholder="Auto"
              />
            </Field>
            <Field label="Local transcription model path">
              <input
                value={media.transcription_model}
                onChange={(e) => onMedia({ ...media, transcription_model: e.target.value })}
                placeholder="Managed by installation"
              />
            </Field>
          </div>
          <Field label="Transcription Python path">
            <input
              value={media.transcription_python}
              onChange={(e) => onMedia({ ...media, transcription_python: e.target.value })}
              placeholder="Managed by installation"
            />
          </Field>
          <Field label="Effects catalog path">
            <input
              value={media.asset_catalog}
              onChange={(e) => onMedia({ ...media, asset_catalog: e.target.value })}
              placeholder="Use the workspace library"
            />
          </Field>
          <Field label="Effects media directory">
            <input
              value={media.asset_root}
              onChange={(e) => onMedia({ ...media, asset_root: e.target.value })}
              placeholder="Use the catalog directory"
            />
          </Field>
          <Field label="Editorial preferences file">
            <input
              value={media.editorial_preferences}
              onChange={(e) => onMedia({ ...media, editorial_preferences: e.target.value })}
              placeholder="Optional existing preferences"
            />
          </Field>
        </Advanced>
      )}
      {perception && provider && provider !== 'none' && (
        <Advanced title="Additional media model limits">
          {provider === 'ollama' && (
            <Field label="Perception Ollama endpoint">
              <input
                type="url"
                value={perception.ollama_url}
                onChange={(e) => onPerception({ ...perception, ollama_url: e.target.value })}
                required
              />
            </Field>
          )}
          <div className="form-grid">
            <NumberField
              label="Perception timeout (seconds)"
              value={perception.timeout_seconds}
              min={15}
              max={1200}
              onChange={(v) => onPerception({ ...perception, timeout_seconds: v })}
            />
            <NumberField
              label="Maximum perception duration (seconds)"
              value={perception.max_seconds}
              min={5}
              max={180}
              onChange={(v) => onPerception({ ...perception, max_seconds: v })}
            />
            <NumberField
              label="Maximum perception upload (MB)"
              value={perception.max_megabytes}
              min={1}
              max={40}
              onChange={(v) => onPerception({ ...perception, max_megabytes: v })}
            />
          </div>
          {provider === 'command' && (
            <CommandField
              label="Custom perception command arguments"
              value={perception.command}
              onChange={(v) => onPerception({ ...perception, command: v })}
              onInvalidEdit={onInvalidEdit}
            />
          )}
        </Advanced>
      )}
    </>
  );
}
