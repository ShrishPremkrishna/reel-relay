import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, request } from '../lib/api';
import {
  readinessSchema,
  setupSchema,
  workspaceSchema,
  type Readiness,
  type Setup,
  type Workspace,
} from '../lib/models';

interface InFlight {
  controller: AbortController;
  promise: Promise<void>;
}

export function useWorkspace() {
  const [state, setState] = useState<Workspace | null>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [updated, setUpdated] = useState(0);
  const inFlight = useRef<InFlight | null>(null);
  const alive = useRef(true);
  const lastFailure = useRef<Error | null>(null);

  // Polls share a request. A refresh after a mutation waits for any old
  // snapshot, then fetches again so a successful save cannot restore old data.
  const refresh = useCallback(async function refresh(force = false): Promise<void> {
    if (inFlight.current) {
      if (!force) return inFlight.current.promise;
      await inFlight.current.promise;
    }
    if (!alive.current) return;
    if (inFlight.current) return refresh(force);
    const controller = new AbortController();
    const task: InFlight = { controller, promise: Promise.resolve() };
    inFlight.current = task;
    setRefreshing(true);
    task.promise = (async () => {
      try {
        const [snapshot, settings, checks] = await Promise.all([
          request('/api/state', workspaceSchema, { signal: controller.signal }),
          request('/api/setup', setupSchema, { signal: controller.signal }),
          request('/api/readiness', readinessSchema, { signal: controller.signal }).catch((e) => {
            if (e instanceof ApiError && e.status === 404) return null;
            throw e;
          }),
        ]);
        if (alive.current && !controller.signal.aborted) {
          setState(snapshot);
          setSetup(settings);
          setReadiness(checks);
          lastFailure.current = null;
          setError(null);
          setUpdated(Date.now());
        }
      } catch (e) {
        if (alive.current && !controller.signal.aborted) {
          lastFailure.current = e instanceof Error ? e : new Error(String(e));
          setError(lastFailure.current);
        }
      } finally {
        if (inFlight.current === task) {
          inFlight.current = null;
          if (alive.current) {
            setLoading(false);
            setRefreshing(false);
          }
        }
      }
    })();
    return task.promise;
  }, []);
  const reload = useCallback(() => refresh(true), [refresh]);
  // Mutations must not clear a draft if its confirming snapshot could not load.
  const reloadChecked = useCallback(async () => {
    await refresh(true);
    if (lastFailure.current) throw lastFailure.current;
  }, [refresh]);
  useEffect(() => {
    alive.current = true;
    void refresh();
    const timer = setInterval(() => {
      if (document.visibilityState !== 'hidden') void refresh();
    }, 5000);
    const onFocus = () => {
      if (document.visibilityState !== 'hidden') void refresh();
    };
    document.addEventListener('visibilitychange', onFocus);
    return () => {
      alive.current = false;
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onFocus);
      inFlight.current?.controller.abort();
      inFlight.current = null;
    };
  }, [refresh]);
  return {
    state,
    setup,
    readiness,
    error,
    loading,
    refreshing,
    updated,
    reload,
    reloadChecked,
    needsLogin: error instanceof ApiError && error.status === 401,
  };
}
