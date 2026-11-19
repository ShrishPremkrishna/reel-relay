import { KeyRound, Volume2 } from 'lucide-react';
import type { SetupDraft } from '../lib/models';
import { Badge, Field } from './primitives';

type PerceptionDraft = Pick<
  SetupDraft,
  'perception_provider' | 'perception_model' | 'allow_cloud_upload' | 'gemini_api_key'
>;
const help = {
  none: 'Sound and motion checks stay unconfigured. Exports requiring those checks remain held for review.',
  ollama:
    'Uses your local model server. Choose an installed model with native audio input; image support alone does not establish audio support.',
  gemini:
    'Sends source media to the Gemini API for perception. This requires an API key and your explicit permission to upload clips.',
  command:
    'Uses the trusted media-review adapter configured on your server. Its argument array can be configured under Additional model limits.',
};

export function PerceptionFields({
  config,
  keySaved,
  selectionMode,
  showCredential = true,
  onChange,
}: {
  config: PerceptionDraft;
  keySaved: boolean;
  selectionMode?: SetupDraft['selection_mode'];
  showCredential?: boolean;
  onChange: (change: Partial<PerceptionDraft>) => void;
}) {
  const provider = config.perception_provider || 'none';
  const throughput = selectionMode === 'throughput';
  return (
    <div className="perception-settings">
      <div className="subsection-heading">
        <Volume2 size={19} />
        <h3>{throughput ? 'Additional media review' : 'Sound & motion review'}</h3>
      </div>
      <p className="field-help">
        {throughput
          ? 'Steady volume uses automatic local checks for rendered speech, caption agreement, audio levels and decoding. The optional model below is used with More selective.'
          : 'Choose how the editor inspects media beyond still frames. This model can be different from your editorial model.'}
      </p>
      <div className="form-grid">
        <Field label="Media perception provider">
          <select
            value={provider}
            onChange={(e) =>
              onChange({
                perception_provider: e.target.value as PerceptionDraft['perception_provider'],
              })
            }
          >
            <option value="none">Not configured</option>
            <option value="ollama">Local model · Ollama</option>
            <option value="gemini">Gemini API · cloud</option>
            <option value="command">Custom media adapter</option>
          </select>
        </Field>
        {provider !== 'none' && (
          <Field label="Perception model (optional)">
            <input
              value={config.perception_model || ''}
              onChange={(e) => onChange({ perception_model: e.target.value })}
              placeholder="Use provider default"
              maxLength={200}
            />
          </Field>
        )}
      </div>
      <p className="field-help">
        {throughput && provider === 'none' ? (
          'No additional media model is configured. Steady volume still runs its local speech, caption and export checks.'
        ) : (
          <>
            {throughput && 'For More selective: '}
            {help[provider]}
          </>
        )}
      </p>
      {provider === 'gemini' && (
        <>
          {showCredential && (
            <>
              <div className="key-status">
                <KeyRound size={15} />
                <span>Gemini API key</span>
                <Badge tone={keySaved ? 'success' : 'warning'}>
                  {keySaved ? 'Saved on server' : 'Key needed'}
                </Badge>
              </div>
              <Field
                label="Gemini API key"
                hint="Saved encrypted on your server. Leave blank to keep the existing key."
              >
                <input
                  type="password"
                  value={config.gemini_api_key || ''}
                  onChange={(e) => onChange({ gemini_api_key: e.target.value })}
                  autoComplete="new-password"
                  placeholder={keySaved ? 'Saved · leave blank to keep' : 'Paste your API key'}
                />
              </Field>
            </>
          )}
          <label className="check-field">
            <input
              type="checkbox"
              checked={config.allow_cloud_upload || false}
              onChange={(e) => onChange({ allow_cloud_upload: e.target.checked })}
            />
            <span>I allow source clips and audio to be sent to Gemini for media review.</span>
          </label>
          {!config.allow_cloud_upload && (
            <p className="field-help">
              Cloud review remains blocked until you grant permission. Saving an API key alone does
              not allow uploads.
            </p>
          )}
        </>
      )}
      {provider === 'ollama' && (
        <p className="field-help">
          No cloud API key is needed. A configured provider is not a guarantee that a particular
          model can hear or understand a clip.
        </p>
      )}
    </div>
  );
}
