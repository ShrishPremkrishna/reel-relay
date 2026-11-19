import type { AgentControls, ContentFlag } from '../../lib/settingsSchema';
import type { Setup } from '../../lib/models';
import { Badge, Field } from '../primitives';
import { Advanced, CommandField, NumberField, Toggle } from './fields';

export function AgentFields({
  value,
  flags,
  provider,
  onChange,
  onInvalidEdit,
}: {
  value: AgentControls;
  flags: ContentFlag[];
  provider: Setup['provider'];
  onChange: (value: AgentControls) => void;
  onInvalidEdit: () => void;
}) {
  const known = new Set(flags.map((f) => f.id));
  const unknown = value.allowed_flags.filter((id) => !known.has(id));
  return (
    <>
      {provider === 'gemini' && (
        <Toggle
          label="Allow Gemini editorial uploads"
          description="Allow clip frames and transcripts to be sent to Gemini for story selection and visual review. The shared API key is configured below."
          checked={value.allow_cloud_upload || false}
          onChange={(v) => onChange({ ...value, allow_cloud_upload: v })}
        />
      )}
      <Advanced
        title="Content you allow"
        description="Allowed flags can continue through the normal checks. Everything marked Hold stays out of automatic publication."
      >
        <div className="content-flag-list">
          {flags.map((flag) => {
            const allowed = value.allowed_flags.includes(flag.id);
            return (
              <label className="content-flag" key={flag.id}>
                <input
                  type="checkbox"
                  aria-label={`Allow ${flag.label}`}
                  checked={allowed}
                  onChange={(e) =>
                    onChange({
                      ...value,
                      allowed_flags: e.target.checked
                        ? [...value.allowed_flags, flag.id]
                        : value.allowed_flags.filter((id) => id !== flag.id),
                    })
                  }
                />
                <span>
                  <strong>{flag.label}</strong>
                  <small>{flag.description}</small>
                </span>
                <Badge tone={allowed ? 'success' : 'warning'}>{allowed ? 'Allow' : 'Hold'}</Badge>
              </label>
            );
          })}
        </div>
        {!!unknown.length && (
          <p className="field-help">
            Unrecognized flags stay held, even if an older configuration listed them as allowed:{' '}
            {unknown.join(', ')}.
          </p>
        )}
        {!flags.length && (
          <p className="field-help">The server has not supplied its content flag catalog.</p>
        )}
      </Advanced>
      <Advanced
        title="Model limits & reliability"
        description="Control editing capacity and retry cost. These settings do not change your posting ceiling."
      >
        <div className="form-grid">
          <NumberField
            label="Maximum editorial jobs per hour"
            value={value.max_jobs_per_hour}
            min={1}
            max={1000}
            onChange={(v) => onChange({ ...value, max_jobs_per_hour: v })}
          />
          <NumberField
            label="Editor timeout (seconds)"
            value={value.timeout_seconds}
            min={15}
            max={3600}
            onChange={(v) => onChange({ ...value, timeout_seconds: v })}
          />
          <NumberField
            label="Minimum confidence"
            value={value.min_confidence}
            min={0}
            max={1}
            step={0.05}
            onChange={(v) => onChange({ ...value, min_confidence: v })}
          />
          <NumberField
            label="More selective score threshold"
            value={value.min_score}
            min={0}
            max={10}
            step={0.1}
            onChange={(v) => onChange({ ...value, min_score: v })}
          />
          <NumberField
            label="Maximum edit revisions"
            value={value.max_revisions}
            min={0}
            max={5}
            onChange={(v) => onChange({ ...value, max_revisions: v })}
          />
          <NumberField
            label="Maximum failed-job retries"
            value={value.max_retries}
            min={0}
            max={10}
            onChange={(v) => onChange({ ...value, max_retries: v })}
          />
        </div>
        {provider === 'ollama' && (
          <>
            <Field label="Editorial Ollama endpoint">
              <input
                type="url"
                value={value.ollama_url}
                onChange={(e) => onChange({ ...value, ollama_url: e.target.value })}
                required
              />
            </Field>
            <div className="form-grid">
              <NumberField
                label="Ollama context tokens"
                value={value.ollama_context_tokens}
                min={4096}
                max={131072}
                onChange={(v) => onChange({ ...value, ollama_context_tokens: v })}
              />
              <NumberField
                label="Ollama maximum output tokens"
                value={value.ollama_output_tokens}
                min={512}
                max={16384}
                onChange={(v) => onChange({ ...value, ollama_output_tokens: v })}
              />
            </div>
          </>
        )}
        {provider === 'command' && (
          <CommandField
            label="Custom editor command arguments"
            value={value.command}
            onChange={(v) => onChange({ ...value, command: v })}
            onInvalidEdit={onInvalidEdit}
          />
        )}
      </Advanced>
    </>
  );
}
