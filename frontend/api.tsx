import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { readWorkspaceResponse } from './dataResponse';

export const pagesBuild = import.meta.env.MODE === 'pages';
const viewOrigin = import.meta.env.GARDIFUND_VIEW_ORIGIN as string;

// The server selects this scope from the actual listening socket, never from a
// caller-supplied hostname. Pages always uses the public viewing routes.
let scope: Promise<'local' | 'remote'> | undefined;
function accessScope() {
  if (pagesBuild) return viewOrigin ? Promise.resolve('remote' as const) : Promise.reject(new Error('Dashboard connection unavailable.'));
  if (!scope) scope = fetch('/scope').then(readWorkspaceResponse).then(value => {
    if (value.access_scope !== 'local' && value.access_scope !== 'remote') throw new Error('The workspace data connection is not configured. Connect the data service, then reload.');
    return value.access_scope as 'local' | 'remote';
  }).catch(error => { scope = undefined; throw error; });
  return scope;
}

function endpoint(scope: 'local' | 'remote', path: string) {
  if (pagesBuild) return `${viewOrigin}/view${path}`;
  return `${scope === 'local' ? '/api' : '/view'}${path}`;
}

export async function api(path: string, init: RequestInit = {}) {
  const route = endpoint(await accessScope(), path);
  if (pagesBuild) {
    if (init.method && !['GET', 'HEAD'].includes(init.method)) throw new Error('Read-only dashboard.');
    return readWorkspaceResponse(await fetch(route, { method: init.method, credentials: 'omit', redirect: 'error', signal: init.signal ?? AbortSignal.timeout(10000) }));
  }
  const headers = new Headers(init.headers);
  if (init.method && !['GET', 'HEAD'].includes(init.method)) {
    headers.set('X-Gardifund-Request', '1');
    if (!(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(route, { ...init, headers });
  return readWorkspaceResponse(response);
}

type Context = { state: any; stateUnavailable: boolean; revision: number; resourceVersions:Record<string,number>; connected: boolean; refresh: () => void; mode: 'live' | 'shadow'; setMode: (v: 'live' | 'shadow') => void; theme:string; setTheme:(v:'dark'|'light') => void };
const AppContext = createContext<Context>(null!);
export const useApp = () => useContext(AppContext);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<any>(null);
  const [stateUnavailable, setStateUnavailable] = useState(false);
  const [revision, setRevision] = useState(0);
  const [stateRevision, setStateRevision] = useState(0);
  const [resourceVersions, setResourceVersions] = useState<Record<string,number>>({});
  const [connected, setConnected] = useState(false);
  const [mode, setMode] = useState<'live' | 'shadow'>('live');
  const [themeOverride, setThemeOverride] = useState(() => { try { const value = localStorage.getItem('gardifund-theme'); return value === 'light' || value === 'dark' ? value : ''; } catch { return ''; } });
  const theme = themeOverride || state?.settings?.appearance || 'light';
  const setTheme = useCallback((value:'dark'|'light') => { setThemeOverride(value); try { localStorage.setItem('gardifund-theme',value); } catch { /* The selection still applies for this session. */ } },[]);
  const refresh = useCallback(() => setRevision(v => v + 1), []);
  useEffect(() => {
    let active = true;
    api('/state').then(value => {
      if (!active) return;
      if (pagesBuild && value.access_scope !== 'remote') throw new Error('Dashboard scope unavailable.');
      setState(value);
      setStateUnavailable(false);
    }).catch(() => {
      if (!active) return;
      setState(null);
      setStateUnavailable(true);
    });
    return () => { active = false; };
  }, [revision, stateRevision]);
  useEffect(() => {
    let source: EventSource | undefined;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let retryDelay = 3000;
    function reconnect() {
      if (!active) return;
      retry = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    }
    // Re-read the current state after reconnecting so an event missed during a
    // disconnection cannot leave a temporary discovery snapshot stale.
    function connect() {
      accessScope().then(value => {
        if (!active) return;
        const stream = new EventSource(endpoint(value, '/events'));
        source = stream;
        stream.onopen = () => { retryDelay = 3000; setConnected(true); refresh(); };
        stream.onerror = () => {
          setConnected(false);
          // A non-200 response closes EventSource instead of retrying on its own.
          if (stream.readyState === EventSource.CLOSED) reconnect();
        };
        stream.onmessage = message => {
          try {
            const event = JSON.parse(message.data);
            if (event.module === 'refresh') refresh();
            else if (typeof event.module === 'string') setResourceVersions(current => ({...current,[event.module]:(current[event.module] || 0)+1}));
          } catch { /* A malformed event must not prevent the normal refresh. */ }
          if (timer) clearTimeout(timer);
          timer = setTimeout(() => setStateRevision(value => value + 1), 200);
        };
      }).catch(() => {
        if (!active) return;
        setConnected(false);
        reconnect();
      });
    }
    connect();
    return () => {
      active = false;
      source?.close();
      if (timer) clearTimeout(timer);
      if (retry) clearTimeout(retry);
    };
  }, [refresh]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#eef2eb' : '#111713');
  }, [theme]);
  return <AppContext.Provider value={{ state, stateUnavailable, revision, resourceVersions, connected, refresh, mode, setMode, theme, setTheme }}>{children}</AppContext.Provider>;
}

export function useResource(path: string | null) {
  const { revision, resourceVersions } = useApp();
  const [name] = path?.split('?') ?? [];
  const modules = name === '/overview' || name === '/overview/summary' || name === '/positions' ? ['overview', 'investing']
    : name === '/setup-access' ? [] : ['database', 'settings'];
  const version = modules.reduce((sum, key) => sum + (resourceVersions[key] || 0), 0);
  const [result, setResult] = useState<{ path: string | null; data: any; error: string }>({ path, data: null, error: '' });
  useEffect(() => {
    if (!path) return;
    let active = true;
    api(path).then(value => { if (active) setResult({ path, data: value, error: '' }); }).catch(e => { if (active) setResult({ path, data: null, error: e.message }); });
    return () => { active = false; };
  }, [path, revision, version]);
  return result.path === path ? { data: result.data, error: result.error } : { data: null, error: '' };
}

export const money = (n: number | null | undefined) => n == null || !Number.isFinite(n) ? '—' : n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
export const quotePrice = (n: number | null | undefined) => {
  if (n == null || !Number.isFinite(n)) return '—';
  if (n !== 0 && Math.abs(n) < 1e-8) return `$${n.toExponential(4)}`;
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 8 });
};
export const pct = (n: number | null | undefined, digits = 2) => n == null || !Number.isFinite(n) ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
export const when = (ts: number | null | undefined) => ts ? new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : 'Not yet';
