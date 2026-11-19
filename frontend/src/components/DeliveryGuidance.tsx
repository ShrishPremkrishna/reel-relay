import { Globe2 } from 'lucide-react';
import type { Setup } from '../lib/models';
import { Badge, Section } from './primitives';

export function DeliveryGuidance({ setup }: { setup: Setup }) {
  return (
    <Section
      id="delivery"
      title="Video delivery to Instagram"
      description="Instagram fetches the finished MP4 from an HTTPS address. A localhost URL is only reachable on your own machine."
      action={<Globe2 size={21} className="section-icon" />}
    >
      <div className="delivery-status">
        <Badge tone={setup.media_host_configured ? 'success' : 'warning'}>
          {setup.media_host_configured ? 'Delivery configured' : 'HTTPS delivery needed'}
        </Badge>
        {setup.public_base_url && <span>{setup.public_base_url}</span>}
      </div>
      <p className="field-help">
        Choose one of the following paths. These are server settings in <code>config.toml</code>;
        this page does not create hosting or expose your computer.
      </p>
      <details className="disclosure" open={!setup.media_host_configured}>
        <summary>Use an existing HTTPS reverse proxy or tunnel</summary>
        <ol className="delivery-steps">
          <li>
            Route a stable HTTPS hostname to the local service at <code>127.0.0.1:8765</code>. The
            proxy must preserve the host and support video byte-range requests.
          </li>
          <li>
            Set the public origin in your configuration:
            <pre>
              <code>
                {
                  '[web]\nhost = "127.0.0.1"\nport = 8765\npublic_base_url = "https://relay.example.com"'
                }
              </code>
            </pre>
          </li>
          <li>
            Set <code>REEL_RELAY_WEB_TOKEN</code> to a dedicated random value of at least 32
            characters in the service’s private environment. Keep it out of URLs and source control.
          </li>
          <li>
            Restart the workspace with the new environment and configuration. Then open its HTTPS
            address, sign in, and register the matching Instagram callback URL with Meta.
          </li>
        </ol>
        <p className="field-help">
          The workspace keeps API and media access authenticated. Instagram receives time-limited
          signed delivery links. The repository includes <code>deploy/Caddyfile.example</code> and{' '}
          <code>docs/DEPLOYMENT.md</code> for proxy configuration.
        </p>
      </details>
      <details className="disclosure">
        <summary>Use an existing S3-compatible bucket</summary>
        <ol className="delivery-steps">
          <li>
            Choose a dedicated bucket and an HTTPS base URL that can serve its published exports.
            Confirm Instagram can fetch that URL without signing in.
          </li>
          <li>
            Configure the publishing destination:
            <pre>
              <code>
                {
                  '[publish]\ns3_bucket = "your-export-bucket"\npublic_media_base = "https://media.example.com"\ns3_region = "your-bucket-region"\n# Set s3_endpoint if your storage provider requires it.'
                }
              </code>
            </pre>
          </li>
          <li>
            Provide dedicated <code>REEL_RELAY_S3_ACCESS_KEY_ID</code> and{' '}
            <code>REEL_RELAY_S3_SECRET_ACCESS_KEY</code> values in the server’s private environment,
            then restart the workspace. Personal AWS profiles are not used automatically.
          </li>
        </ol>
        <p className="field-help">
          Storage delivery does not replace Instagram’s HTTPS sign-in callback. Configure the
          callback before connecting the account. The bucket route uploads final exports only.
        </p>
      </details>
      <p className="field-help">
        Return to the readiness checks after configuration. A saved delivery address still needs a
        real publishing test with your connected account.
      </p>
    </Section>
  );
}
