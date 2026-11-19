import { Component, useCallback, useEffect, useState, type ErrorInfo, type ReactNode } from 'react';
import {
  Activity,
  ArrowUpRight,
  CheckCircle2,
  Film,
  Library,
  LoaderCircle,
  LockKeyhole,
  Radio,
  RefreshCw,
  Settings2,
  ShieldCheck,
  X,
} from 'lucide-react';
import { Button, Field, Logo, Notice } from './components/primitives';
import { useWorkspace } from './hooks/useWorkspace';
import { message, mutate } from './lib/api';
import { countGroup, isRecording } from './lib/domain';
import type { Workspace } from './lib/models';
import { ActivityPage } from './pages/ActivityPage';
import { LibraryPage } from './pages/LibraryPage';
import { QueuePage } from './pages/QueuePage';
import { SettingsPage } from './pages/SettingsPage';
import { SourcesPage } from './pages/SourcesPage';

type View = 'queue' | 'sources' | 'library' | 'activity' | 'settings';
const navigation: { id: View; label: string; href: string; icon: typeof Film }[] = [
  { id: 'queue', label: 'Clip queue', href: '/', icon: Film },
  { id: 'sources', label: 'Sources', href: '/?view=sources', icon: Radio },
  { id: 'library', label: 'Effects library', href: '/?view=library', icon: Library },
  { id: 'activity', label: 'Activity', href: '/?view=activity', icon: Activity },
  { id: 'settings', label: 'Setup & accounts', href: '/setup', icon: Settings2 },
];
function currentView(): View {
  if (location.pathname === '/setup') return 'settings';
  const view = new URLSearchParams(location.search).get('view');
  return navigation.some((n) => n.id === view) ? (view as View) : 'queue';
}

export default function App() {
  const workspace = useWorkspace();
  const view = currentView();
  const [toast, setToast] = useState('');
  const notify = useCallback((text: string) => setToast(text), []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(''), 6000);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    document.title = `${navigation.find((n) => n.id === view)?.label} · Reel Relay`;
  }, [view]);
  const hasSetup = !!workspace.setup;
  useEffect(() => {
    if (!hasSetup) return;
    const target = location.hash.slice(1);
    if (
      ['runtime', 'sources', 'editor', 'style', 'instagram', 'delivery', 'schedule'].includes(
        target,
      )
    )
      document.getElementById(target)?.scrollIntoView({ block: 'start' });
  }, [view, hasSetup]);
  if (workspace.needsLogin) return <Login onSuccess={workspace.reload} />;
  const { state, setup, readiness, error } = workspace;

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      <Sidebar view={view} state={state} connected={!error && !!state} />
      <div className="main-shell">
        <div className="workspace-bar">
          <span>
            <span
              className={`status-dot ${error ? 'status-dot-warning' : state ? 'status-dot-live' : ''}`}
            />
            {error
              ? 'Connection interrupted'
              : state
                ? 'Workspace connected'
                : 'Connecting to workspace'}
          </span>
          <span className="workspace-location">
            <ShieldCheck size={15} />
            Your server · Your data
          </span>
        </div>
        <main id="main-content" tabIndex={-1}>
          {error && (
            <Notice
              tone="danger"
              action={
                <Button busy={workspace.refreshing} onClick={() => void workspace.reload()}>
                  <RefreshCw size={15} />
                  Retry
                </Button>
              }
            >
              <strong>{error.message}</strong>
              {state && (
                <span>
                  Showing the last saved snapshot. Last update:{' '}
                  {new Date(workspace.updated).toLocaleTimeString()}.
                </span>
              )}
            </Notice>
          )}
          {workspace.loading && !state ? (
            <WorkspaceLoading />
          ) : state && setup ? (
            <>
              {view === 'queue' && (
                <QueuePage
                  state={state}
                  setup={setup}
                  readiness={readiness}
                  refreshing={workspace.refreshing}
                  reload={workspace.reload}
                  notify={notify}
                />
              )}
              {view === 'sources' && <SourcesPage state={state} setup={setup} />}
              {view === 'library' && <LibraryPage csrf={setup.csrf} notify={notify} />}
              {view === 'activity' && <ActivityPage />}
              {view === 'settings' && (
                <SettingsPage
                  setup={setup}
                  readiness={readiness}
                  onChange={workspace.reloadChecked}
                  notify={notify}
                />
              )}
            </>
          ) : (
            !error && <WorkspaceLoading />
          )}
        </main>
        <footer className="workspace-footer">
          <span>Made for the moments worth keeping.</span>
          <span>Reel Relay · Local studio</span>
        </footer>
      </div>
      <div className="toast-region" aria-live="polite" aria-atomic="true">
        {toast && (
          <div className="toast">
            <CheckCircle2 size={18} />
            <span>{toast}</span>
            <button
              className="icon-button"
              aria-label="Dismiss notification"
              onClick={() => setToast('')}
            >
              <X size={16} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function Sidebar({
  view,
  state,
  connected,
}: {
  view: View;
  state: Workspace | null;
  connected: boolean;
}) {
  const recording = connected ? state?.channels.filter((c) => isRecording(c)).length || 0 : 0;
  const publishing = state?.settings.publishing && !state.settings.dry_run;
  return (
    <aside className="sidebar">
      <a className="brand-link" href="/" aria-label="Reel Relay home">
        <Logo />
      </a>
      <div className="sidebar-workspace">
        <span className="workspace-avatar">R</span>
        <div>
          <strong>My workspace</strong>
          <span>Local installation</span>
        </div>
        <span className="local-chip">LOCAL</span>
      </div>
      <nav className="main-nav" aria-label="Main navigation">
        {navigation.map(({ id, label, href, icon: Icon }) => (
          <a
            key={id}
            href={href}
            className={id === view ? 'active' : ''}
            aria-current={id === view ? 'page' : undefined}
          >
            <Icon size={19} />
            <span>{label}</span>
            {id === 'queue' && !!state && countGroup(state, 'review') > 0 && (
              <span className="nav-count">{countGroup(state, 'review')}</span>
            )}
          </a>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <section className="pipeline-status" aria-label="Pipeline status">
          <div>
            <span className={`status-dot ${recording ? 'status-dot-live' : ''}`} />
            <strong>
              {recording
                ? `${recording} source${recording === 1 ? '' : 's'} recording`
                : 'No active recordings'}
            </strong>
          </div>
          <p>{publishing ? 'Publishing is enabled' : 'Publishing is paused'}</p>
          <a href="/setup">
            Manage workflow
            <ArrowUpRight size={16} />
          </a>
        </section>
        <div className="sidebar-footnote">
          <ShieldCheck size={16} />
          <span>Built to run on your terms.</span>
        </div>
      </div>
    </aside>
  );
}
function WorkspaceLoading() {
  return (
    <div className="workspace-loading" role="status">
      <LoaderCircle size={28} className="spin" />
      <h1>Opening your studio</h1>
      <p>Reading the queue and checking your connections…</p>
    </div>
  );
}

export function Login({ onSuccess }: { onSuccess: () => Promise<void> }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit() {
    setBusy(true);
    setError('');
    try {
      await mutate('/api/login', { token });
      setToken('');
      await onSuccess();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-page">
      <a href="/" className="brand-link">
        <Logo />
      </a>
      <section className="panel login-card">
        <span className="login-icon">
          <LockKeyhole size={25} />
        </span>
        <p className="eyebrow">YOUR PRIVATE WORKSPACE</p>
        <h1>Welcome to your studio.</h1>
        <p>Use the access token configured on this Reel Relay server to continue.</p>
        {error && <Notice tone="danger">{error}</Notice>}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <Field label="Server access token">
            <input
              type="password"
              autoComplete="current-password"
              autoFocus
              required
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </Field>
          <Button type="submit" variant="primary" busy={busy}>
            Open workspace
          </Button>
        </form>
        <p className="field-help">
          Your token is exchanged for a secure session cookie. It is not stored in browser storage
          or placed in a URL.
        </p>
      </section>
    </main>
  );
}

export class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {
    /* Avoid logging response bodies or account details. */
  }
  render() {
    if (this.state.failed)
      return (
        <main className="login-page">
          <Logo />
          <section className="panel login-card">
            <h1>The interface needs a refresh.</h1>
            <p>The background pipeline runs separately. Reload this page to reconnect to it.</p>
            <Button onClick={() => location.reload()}>Reload workspace</Button>
          </section>
        </main>
      );
    return this.props.children;
  }
}
