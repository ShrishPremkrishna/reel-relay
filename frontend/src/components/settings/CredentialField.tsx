import { KeyRound } from 'lucide-react';
import { Badge, Field } from '../primitives';
import { Toggle } from './fields';

export function CredentialField({
  label,
  value,
  saved,
  source,
  clear = false,
  onChange,
  onClear,
}: {
  label: string;
  value: string | null | undefined;
  saved: boolean;
  source?: 'saved' | 'environment' | 'none';
  clear?: boolean;
  onChange: (value: string) => void;
  onClear?: (value: boolean) => void;
}) {
  const environment = source === 'environment';
  return (
    <div className="credential-field">
      <div className="key-status">
        <KeyRound size={15} />
        <span>{label}</span>
        <Badge tone={saved ? 'success' : 'neutral'}>
          {environment ? 'Managed by host' : saved ? 'Saved on server' : 'Not saved'}
        </Badge>
      </div>
      <Field
        label={label}
        hint={
          environment
            ? 'This credential is supplied by the server environment and cannot be replaced or removed here.'
            : 'Stored encrypted. Leave blank to keep the saved value; it is never returned to this page.'
        }
      >
        <input
          type="password"
          value={value || ''}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="new-password"
          disabled={environment || clear}
          placeholder={
            clear
              ? 'Removed on next save'
              : saved
                ? 'Saved · leave blank to keep'
                : 'Paste credential'
          }
        />
      </Field>
      {saved && onClear && (
        <Toggle
          label={`Remove saved ${label}`}
          description={
            environment
              ? 'Host-managed credentials must be changed by the server administrator.'
              : 'Takes effect when you save settings.'
          }
          checked={clear}
          disabled={environment}
          onChange={onClear}
        />
      )}
    </div>
  );
}
