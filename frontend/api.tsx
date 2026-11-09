import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { readWorkspaceResponse } from './dataResponse';

// The server selects this scope from the actual listening socket, never from a
// caller-supplied hostname. The result only selects the presentation URLs.
let scope: Promise<'local' | 'remote'> | undefined;
function accessScope() {
  if (!scope) scope = fetch('/scope').then(readWorkspaceResponse).then(value => {
    if (value.access_scope !== 'local' && value.access_scope !== 'remote') throw new Error('The workspace data connection is not configured. Connect the data service, then reload.');
    return value.access_scope as 'local' | 'remote';
  }).catch(error => { scope = undefined; throw error; });
  return scope;
}

export async function api(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (init.method && !['GET', 'HEAD'].includes(init.method)) {
    headers.set('X-Gardifund-Request', '1');
    if (!(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(`${await accessScope() === 'local' ? '/api' : '/view'}${path}`, { ...init, headers });
  return readWorkspaceResponse(response);
}

type Context = { state: any; revision: number; resourceVersions:Record<string,number>; marketVersions: Record<string, number>; connected: boolean; refresh: () => void; mode: 'live' | 'shadow'; setMode: (v: 'live' | 'shadow') => void; theme:string; setTheme:(v:'dark'|'light') => void };
const AppContext = createContext<Context>(null!);
export const useApp = () => useContext(AppContext);

export function AppProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<any>(null);
  const [revision, setRevision] = useState(0);
  const [stateRevision, setStateRevision] = useState(0);
  const [resourceVersions, setResourceVersions] = useState<Record<string,number>>({});
  const [marketVersions, setMarketVersions] = useState<Record<string, number>>({});
  const [connected, setConnected] = useState(false);
  const [mode, setMode] = useState<'live' | 'shadow'>('live');
  const [themeOverride, setThemeOverride] = useState(() => { try { const value = localStorage.getItem('gardifund-theme'); return value === 'light' || value === 'dark' ? value : ''; } catch { return ''; } });
  const theme = themeOverride || state?.settings?.appearance || 'light';
  const setTheme = useCallback((value:'dark'|'light') => { setThemeOverride(value); try { localStorage.setItem('gardifund-theme',value); } catch { /* The selection still applies for this session. */ } },[]);
  const refresh = useCallback(() => setRevision(v => v + 1), []);
  useEffect(() => { api('/state').then(setState).catch(() => setState(null)); }, [revision, stateRevision]);
  useEffect(() => {
    let source: EventSource | undefined;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    // Re-read the current state after reconnecting so an event missed during a
    // disconnection cannot leave a temporary discovery snapshot stale.
    accessScope().then(value => {
      if (!active) return;
      source = new EventSource(`${value === 'local' ? '/api' : '/view'}/events`);
      source.onopen = () => { setConnected(true); refresh(); };
      source.onerror = () => setConnected(false);
      source.onmessage = message => {
      try {
        const event = JSON.parse(message.data);
        if (event.module === 'refresh') refresh();
        else if (typeof event.module === 'string') setResourceVersions(current => ({...current,[event.module]:(current[event.module] || 0)+1}));
        if (event.module === 'charts' && event.action === 'market_updated' && typeof event.payload?.symbol === 'string' && typeof event.payload?.interval === 'string') {
          const key = `${event.payload.symbol}:${event.payload.interval}`;
          setMarketVersions(current => ({ ...current, [key]: (current[key] || 0) + 1 }));
        }
      } catch { /* A malformed event must not prevent the normal refresh. */ }
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => setStateRevision(value => value + 1), 200);
      };
    }).catch(() => setConnected(false));
    return () => { active = false; source?.close(); if (timer) clearTimeout(timer); };
  }, [refresh]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#eef2eb' : '#111713');
  }, [theme]);
  return <AppContext.Provider value={{ state, revision, resourceVersions, marketVersions, connected, refresh, mode, setMode, theme, setTheme }}>{children}</AppContext.Provider>;
}

export function useResource(path: string | null) {
  const { revision, resourceVersions, marketVersions } = useApp();
  const name = path?.split('?')[0] || '';
  let version = 0;
  if (name === '/bars') {
    const query = new URLSearchParams(path?.split('?')[1]);
    version = marketVersions[`${query.get('symbol') || 'SPY'}:${query.get('interval') || '1d'}`] || 0;
  } else {
    const modules = name === '/overview' || name === '/positions' ? ['overview','investing']
      : name === '/chart-performance' ? ['charts','database'] : name === '/sectors' ? ['investing','overview','charts']
      : name === '/setup-access' ? [] : ['database','settings','charts'];
    version = modules.reduce((sum,key) => sum + (resourceVersions[key] || 0),0);
  }
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
