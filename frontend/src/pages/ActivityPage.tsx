import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity as ActivityIcon,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  RefreshCw,
} from 'lucide-react';
import { message, request } from '../lib/api';
import { activitySchema, type Activity } from '../lib/models';
import { Button, EmptyState, Notice, PageHeader } from '../components/primitives';

export function ActivityPage() {
  const [items, setItems] = useState<Activity['items']>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef<AbortController | null>(null);
  const load = useCallback(async (before?: number) => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setLoading(true);
    setError('');
    try {
      const data = await request(
        '/api/activity?limit=30' + (before ? '&before=' + before : ''),
        activitySchema,
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setItems((old) =>
        before
          ? [...old, ...data.items.filter((item) => !old.some((o) => o.id === item.id))]
          : data.items,
      );
      setCursor(data.next_before);
    } catch (e) {
      if (!controller.signal.aborted) setError(message(e));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
    return () => inFlight.current?.abort();
  }, [load]);
  return (
    <>
      <PageHeader
        eyebrow="WORKSPACE HISTORY"
        title="Know what happened."
        description="Capture, editing and publishing decisions, with a clear trail back to each clip."
        action={
          <Button busy={loading} onClick={() => void load()}>
            <RefreshCw size={16} />
            Refresh
          </Button>
        }
      />
      {error && (
        <Notice tone="danger" action={<Button onClick={() => void load()}>Retry</Button>}>
          {error}
        </Notice>
      )}
      <section className="panel activity-panel">
        {!items.length && !loading && !error && (
          <EmptyState
            icon={<ActivityIcon size={28} />}
            title="A clean slate"
            description="Events will appear here as the workspace captures and edits its first moments."
          />
        )}
        {loading && !items.length && (
          <p className="loading-panel" role="status">
            Loading activity…
          </p>
        )}
        <ol className="activity-list">
          {items.map((item) => (
            <li key={item.id}>
              <span className={`activity-symbol ${item.level}`}>
                {item.level === 'info' ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
              </span>
              <div>
                <div className="activity-kind">
                  {item.kind.replaceAll('_', ' ')}
                  <time dateTime={new Date(item.at * 1000).toISOString()}>
                    {new Date(item.at * 1000).toLocaleString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      hour: 'numeric',
                      minute: '2-digit',
                    })}
                  </time>
                </div>
                <p>{item.summary}</p>
                {item.candidate_id && (
                  <a className="text-link" href={`/?clip=${encodeURIComponent(item.candidate_id)}`}>
                    Open clip <code>{item.candidate_id.slice(0, 8)}</code>
                  </a>
                )}
              </div>
            </li>
          ))}
        </ol>
        {cursor !== null && (
          <div className="activity-more">
            <Button busy={loading} onClick={() => void load(cursor)}>
              <ChevronDown size={16} />
              Load earlier activity
            </Button>
          </div>
        )}
      </section>
    </>
  );
}
