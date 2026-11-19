import { Globe2 } from 'lucide-react';
import type { DeliverySettings } from '../../lib/settingsSchema';
import type { Setup, SetupDraft } from '../../lib/models';
import { Badge, Field, Section } from '../primitives';
import { Advanced, Toggle } from './fields';

export function DeliveryFields({
  value,
  setup,
  secrets,
  onChange,
  onSecrets,
}: {
  value: DeliverySettings;
  setup: Setup;
  secrets: Pick<SetupDraft, 's3_access_key_id' | 's3_secret_access_key' | 'clear_s3_credentials'>;
  onChange: (value: DeliverySettings) => void;
  onSecrets: (
    value: Partial<
      Pick<SetupDraft, 's3_access_key_id' | 's3_secret_access_key' | 'clear_s3_credentials'>
    >,
  ) => void;
}) {
  const environment = setup.credential_sources?.s3 === 'environment';
  return (
    <Section
      id="delivery"
      title="Video delivery"
      description="Choose how Instagram fetches finished videos. Localhost addresses cannot be reached by Instagram."
      action={<Globe2 size={21} className="section-icon" />}
    >
      <div className="delivery-status">
        <Badge tone={setup.media_host_configured ? 'success' : 'warning'}>
          {setup.media_host_configured ? 'Delivery configured' : 'Set up HTTPS delivery'}
        </Badge>
        <span>Use your HTTPS workspace or an existing storage bucket.</span>
      </div>
      <Field
        label="Public workspace HTTPS origin"
        hint="An existing HTTPS address pointing to this workspace, with no path. Changing it requires a server restart."
      >
        <input
          type="url"
          value={value.public_base_url}
          onChange={(e) => onChange({ ...value, public_base_url: e.target.value })}
          placeholder="https://relay.example.com"
          maxLength={2000}
        />
      </Field>
      <p className="field-help">
        Your host must already route this address to Reel Relay and provide its access token. Saving
        an address does not create a domain or tunnel.
      </p>
      <Advanced
        title="Use S3-compatible storage"
        description="Provide a dedicated export bucket and an HTTPS URL that can serve its reels/ objects. Saving these details does not create or make a bucket public."
        open={!!value.s3_bucket}
      >
        <div className="form-grid">
          <Field label="Export bucket">
            <input
              value={value.s3_bucket}
              onChange={(e) => onChange({ ...value, s3_bucket: e.target.value })}
              autoComplete="off"
            />
          </Field>
          <Field label="Storage region">
            <input
              value={value.s3_region}
              onChange={(e) => onChange({ ...value, s3_region: e.target.value })}
              placeholder="Your bucket region"
            />
          </Field>
        </div>
        <Field label="Public media base URL">
          <input
            type="url"
            value={value.public_media_base}
            onChange={(e) => onChange({ ...value, public_media_base: e.target.value })}
            placeholder="https://media.example.com"
          />
        </Field>
        <Field label="Storage endpoint (optional)">
          <input
            type="url"
            value={value.s3_endpoint}
            onChange={(e) => onChange({ ...value, s3_endpoint: e.target.value })}
            placeholder="Provider endpoint for compatible storage"
          />
        </Field>
        <div className="key-status">
          <span>Storage credentials</span>
          <Badge tone={setup.s3_credentials_saved ? 'success' : 'neutral'}>
            {environment
              ? 'Managed by host'
              : setup.s3_credentials_saved
                ? 'Saved on server'
                : 'Not saved'}
          </Badge>
        </div>
        <div className="form-grid">
          <Field label="Storage access key ID">
            <input
              type="password"
              value={secrets.s3_access_key_id || ''}
              disabled={environment || secrets.clear_s3_credentials}
              onChange={(e) =>
                onSecrets({ s3_access_key_id: e.target.value, clear_s3_credentials: false })
              }
              autoComplete="new-password"
              placeholder={
                setup.s3_credentials_saved ? 'Saved · leave blank to keep' : 'Dedicated access key'
              }
            />
          </Field>
          <Field label="Storage secret access key">
            <input
              type="password"
              value={secrets.s3_secret_access_key || ''}
              disabled={environment || secrets.clear_s3_credentials}
              onChange={(e) =>
                onSecrets({ s3_secret_access_key: e.target.value, clear_s3_credentials: false })
              }
              autoComplete="new-password"
              placeholder={
                setup.s3_credentials_saved ? 'Saved · leave blank to keep' : 'Dedicated secret key'
              }
            />
          </Field>
        </div>
        <p className="field-help">
          {environment
            ? 'These credentials are supplied by the host environment. Ask the server administrator to change them.'
            : 'Keys are encrypted on the server. Personal AWS profiles are not used automatically.'}
        </p>
        {setup.s3_credentials_saved && (
          <Toggle
            label="Remove saved storage credentials"
            description="Takes effect when you save settings."
            checked={secrets.clear_s3_credentials || false}
            disabled={environment}
            onChange={(v) =>
              onSecrets({
                clear_s3_credentials: v,
                s3_access_key_id: null,
                s3_secret_access_key: null,
              })
            }
          />
        )}
        <p className="field-help">
          Set a retention policy for this dedicated bucket’s reels/ prefix. Remote export cleanup is
          managed by your storage provider.
        </p>
      </Advanced>
      <Advanced title="Instagram API compatibility">
        <Field label="Instagram API version">
          <input
            value={value.api_version}
            onChange={(e) => onChange({ ...value, api_version: e.target.value })}
            pattern="v[0-9]+\.[0-9]+"
            required
            placeholder="v24.0"
          />
        </Field>
        <p className="field-help">
          Change this only when your Meta application requires a supported API version.
        </p>
      </Advanced>
    </Section>
  );
}
