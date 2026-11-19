import { useEffect, useMemo, useState } from 'react';
import { AudioLines, ChevronDown, Film, Library, Search } from 'lucide-react';
import { message, request } from '../lib/api';
import { assetsSchema, type Asset } from '../lib/models';
import { MediaIntake } from '../components/MediaIntake';
import { duration } from '../lib/domain';
import { Badge, Button, EmptyState, Notice, PageHeader } from '../components/primitives';

export function LibraryPage({
  csrf,
  notify = () => {},
}: {
  csrf?: string;
  notify?: (message: string) => void;
}) {
  const [items, setItems] = useState<Asset[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState('all');
  const [attempt, setAttempt] = useState(0);
  const [visible, setVisible] = useState(24);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    request('/api/assets', assetsSchema, { signal: controller.signal })
      .then((data) => {
        setItems(data.items);
        setWarnings(data.warnings);
      })
      .catch((e) => {
        if (e.name !== 'AbortError') setError(message(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    setVisible(24);
  }, [search, kind]);
  const kinds = Array.from(new Set(items.map((a) => a.kind)));
  const matches = useMemo(
    () =>
      items.filter(
        (a) =>
          (kind === 'all' || a.kind === kind) &&
          `${a.title} ${a.tags.join(' ')}`.toLowerCase().includes(search.toLowerCase()),
      ),
    [items, kind, search],
  );
  return (
    <>
      <PageHeader
        eyebrow="THE EDITOR’S TOOLKIT"
        title="Give each beat a purpose."
        description="Real sounds and reactions, chosen for the moment. No effect quota."
        action={
          csrf ? (
            <MediaIntake
              mode="asset"
              csrf={csrf}
              notify={notify}
              onComplete={async () => {
                setAttempt((n) => n + 1);
              }}
            />
          ) : undefined
        }
      />
      {warnings.map((w) => (
        <Notice key={w} tone="warning">
          {w}
        </Notice>
      ))}
      <div className="library-toolbar">
        <label className="search-field">
          <Search size={17} />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search a sound, reaction or mood"
            aria-label="Search effects"
          />
        </label>
        <label className="sr-only" htmlFor="asset-kind">
          Asset type
        </label>
        <select id="asset-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="all">All types</option>
          {kinds.map((k) => (
            <option key={k} value={k}>
              {k === 'sfx' ? 'Sound effects' : k === 'meme' ? 'Reactions' : k}
            </option>
          ))}
        </select>
        <span className="metadata-line">{items.length} assets in this installation</span>
      </div>
      {loading ? (
        <div className="panel loading-panel" role="status">
          Loading your library…
        </div>
      ) : error ? (
        <Notice
          tone="danger"
          action={<Button onClick={() => setAttempt((x) => x + 1)}>Retry</Button>}
        >
          {error}
        </Notice>
      ) : !matches.length ? (
        <section className="panel">
          <EmptyState
            icon={<Library size={28} />}
            title={items.length ? 'No matching effects' : 'Your library is ready to grow'}
            description={
              items.length
                ? 'Try a different mood or asset type.'
                : 'Add a sound, reaction or B-roll file for the editor to choose from. Library contents stay separate from the application code.'
            }
          />
        </section>
      ) : (
        <>
          <div className="asset-grid">
            {matches.slice(0, visible).map((asset) => (
              <AssetCard key={asset.id} asset={asset} />
            ))}
          </div>
          {matches.length > visible && (
            <div className="library-more">
              <p>
                Showing {visible} of {matches.length} matching assets
              </p>
              <Button onClick={() => setVisible((n) => n + 24)}>Load more effects</Button>
            </div>
          )}
        </>
      )}
    </>
  );
}
function AssetCard({ asset: a }: { asset: Asset }) {
  const preview =
    a.preview_url?.startsWith('/') && !a.preview_url.startsWith('//') ? a.preview_url : undefined;
  const audio = a.kind === 'sfx' || a.kind === 'music';
  return (
    <article className="panel asset-card">
      <div className={`asset-preview ${audio ? 'asset-audio' : ''}`}>
        {preview ? (
          a.media_type?.startsWith('image/') ? (
            <img src={preview} alt={a.title} loading="lazy" />
          ) : audio ? (
            <audio src={preview} controls preload="none" aria-label={`Listen to ${a.title}`} />
          ) : (
            <video src={preview} controls preload="metadata" aria-label={`Preview ${a.title}`} />
          )
        ) : (
          <div className="asset-placeholder">
            {audio ? <AudioLines size={30} /> : <Film size={30} />}
            <span>No preview available</span>
          </div>
        )}
      </div>
      <div className="asset-body">
        <div className="asset-kind">
          <Badge>
            {a.kind === 'sfx' ? 'Sound effect' : a.kind === 'meme' ? 'Reaction' : a.kind}
          </Badge>
          <span>{duration(a.duration ?? undefined)}</span>
        </div>
        <h2>{a.title}</h2>
        <div className="asset-tags">
          {a.tags.slice(0, 4).map((t) => (
            <span key={t}>{t}</span>
          ))}
        </div>
        <details className="asset-credits">
          <summary>
            Source & use notes
            <ChevronDown size={15} />
          </summary>
          <p>{a.rights || 'Usage rights have not been recorded.'}</p>
          {a.attribution && <p>{a.attribution}</p>}
        </details>
      </div>
    </article>
  );
}
