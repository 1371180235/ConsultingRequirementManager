import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, ApiError, setCsrfToken } from '../api';
import { roleLabels } from '../mockData';
import type { ToastMessage, User, WorkspaceOptions } from '../types';

export type ThemeName = 'blue' | 'green' | 'warm' | 'dark';
export type AppMode = 'live' | 'demo';

interface AppState {
  user: User | null;
  authReady: boolean;
  authError: string;
  sessionReason: string;
  mode: AppMode;
  theme: ThemeName;
  options: WorkspaceOptions;
  selectedProject: string;
  selectedYear: string;
  selectedVersion: string;
  toasts: ToastMessage[];
  setTheme: (theme: ThemeName) => void;
  setSelectedProject: (id: string) => void;
  setSelectedYear: (id: string) => void;
  setSelectedVersion: (id: string) => void;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, nextPassword: string) => Promise<void>;
  refreshContext: () => Promise<void>;
  clearSessionReason: () => void;
  notify: (title: string, detail?: string, tone?: ToastMessage['tone']) => void;
}

const AppContext = createContext<AppState | null>(null);

function normalizeUser(payload: unknown): User {
  const value = (payload && typeof payload === 'object' && 'user' in payload
    ? (payload as { user: unknown }).user
    : payload) as Partial<User> & { full_name?: string; must_change_password?: boolean };
  return {
    id: String(value.id ?? value.username ?? ''),
    name: value.name ?? value.full_name ?? value.username ?? '用户',
    username: value.username ?? '',
    role: value.role ?? 'customer',
    roleLabel: value.roleLabel ?? String((value as { role_label?: string }).role_label ?? roleLabels[value.role ?? ''] ?? value.role ?? '用户'),
    firstLogin: value.firstLogin ?? value.must_change_password ?? false,
    projects: (value.projects ?? (value as { project_ids?: Array<string | number> }).project_ids)?.map(String),
  };
}

function normalizeOptions(payload: unknown): WorkspaceOptions {
  const source = (payload && typeof payload === 'object' && 'data' in payload
    ? (payload as { data: unknown }).data
    : payload) as {
      projects?: Array<Record<string, unknown>>;
      plans?: Array<Record<string, unknown>>;
      years?: Array<Record<string, unknown>>;
      annual_plans?: Array<Record<string, unknown>>;
      versions?: Array<Record<string, unknown>>;
      tags?: Array<Record<string, unknown>>;
    };
  const projects = (source?.projects ?? []).map((item) => ({
    id: String(item.id),
    name: String(item.name ?? item.code ?? item.id),
    status: item.status ? String(item.status) : undefined,
  }));
  const years = (source?.plans ?? source?.years ?? source?.annual_plans ?? []).map((item) => ({
    id: String(item.id),
    name: String(item.name ?? `${item.year ?? ''} 年度计划`),
    parentId: String(item.project_id ?? item.parentId ?? item.parent_id ?? ''),
    status: item.status ? String(item.status) : undefined,
  }));
  const versions = (source?.versions ?? []).map((item) => ({
    id: String(item.id),
    name: String(item.name ? `${item.code ? `${item.code} ` : ''}${item.name}` : item.code ?? item.id),
    parentId: String(item.annual_plan_id ?? item.plan_id ?? item.parentId ?? item.parent_id ?? ''),
    status: item.status ? String(item.status) : undefined,
  }));
  const tags = (source?.tags ?? []).map((item) => ({ id: String(item.id), name: String(item.name ?? item.id) }));
  return { projects, years, versions, tags };
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState('');
  const [sessionReason, setSessionReason] = useState('');
  const [mode, setMode] = useState<AppMode>('live');
  const [theme, setThemeState] = useState<ThemeName>(() => (localStorage.getItem('crm-theme') as ThemeName) || 'blue');
  const [options, setOptions] = useState<WorkspaceOptions>({ projects: [], years: [], versions: [], tags: [] });
  const [selectedProject, setProject] = useState('');
  const [selectedYear, setYear] = useState('');
  const [selectedVersion, setVersion] = useState('');
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const notify = useCallback((title: string, detail?: string, tone: ToastMessage['tone'] = 'success') => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((current) => [...current, { id, title, detail, tone }].slice(-3));
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 4200);
  }, []);

  const setTheme = useCallback((value: ThemeName) => {
    setThemeState(value);
    localStorage.setItem('crm-theme', value);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    meta?.setAttribute('content', theme === 'dark' ? '#111827' : theme === 'green' ? '#047857' : theme === 'warm' ? '#c2410c' : '#2563eb');
  }, [theme]);

  useEffect(() => {
    if (user) document.documentElement.dataset.role = user.role;
    else delete document.documentElement.dataset.role;
  }, [user]);

  const loadContext = useCallback(async () => {
    try {
      const payload = await api.get<unknown>('/api/context');
      const data = normalizeOptions(payload);
      setOptions(data);
      setProject((current) => data.projects.some((item) => item.id === current) ? current : data.projects[0]?.id ?? '');
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 401)) {
        notify('上下文加载失败', error instanceof Error ? error.message : '请稍后重试', 'danger');
      }
    }
  }, [notify]);

  useEffect(() => {
    let cancelled = false;
    api.get<unknown>('/api/auth/me')
      .then((payload) => {
        if (!cancelled) {
          if (payload && typeof payload === 'object' && 'csrf_token' in payload) {
            setCsrfToken(String((payload as { csrf_token: unknown }).csrf_token));
          }
          setUser(normalizeUser(payload));
          void loadContext();
        }
      })
      .catch((error) => {
        if (!cancelled && !(error instanceof ApiError && error.status === 401)) {
          setAuthError(error instanceof Error ? error.message : '无法连接服务器');
        }
      })
      .finally(() => !cancelled && setAuthReady(true));
    return () => { cancelled = true; };
  }, [loadContext]);

  useEffect(() => {
    const onExpired = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail;
      setUser(null);
      setMode('live');
      setCsrfToken(undefined);
      setSessionReason(detail || '当前会话已失效，可能是账号已在其他位置登录。');
    };
    window.addEventListener('crm:session-expired', onExpired);
    return () => window.removeEventListener('crm:session-expired', onExpired);
  }, []);

  useEffect(() => {
    const years = options.years.filter((item) => item.parentId === selectedProject);
    if (years.length && !years.some((item) => item.id === selectedYear)) setYear(years[0].id);
    if (!years.length && selectedYear) setYear('');
  }, [options.years, selectedProject, selectedYear]);

  useEffect(() => {
    const versions = options.versions.filter((item) => item.parentId === selectedYear);
    if (versions.length && !versions.some((item) => item.id === selectedVersion)) setVersion(versions[0].id);
    if (!versions.length) setVersion('');
  }, [options.versions, selectedYear, selectedVersion]);

  const login = useCallback(async (username: string, password: string) => {
    setAuthError('');
    const payload = await api.post<unknown>('/api/auth/login', { username, password });
    if (payload && typeof payload === 'object' && 'csrf_token' in payload) {
      setCsrfToken(String((payload as { csrf_token: unknown }).csrf_token));
    }
    setMode('live');
    setUser(normalizeUser(payload));
    await loadContext();
  }, [loadContext]);

  const logout = useCallback(async () => {
    if (mode === 'live') {
      await api.post('/api/auth/logout').catch(() => undefined);
    }
    setUser(null);
    setMode('live');
    setCsrfToken(undefined);
  }, [mode]);

  const changePassword = useCallback(async (currentPassword: string, nextPassword: string) => {
    if (mode === 'live') {
      await api.post('/api/auth/change-password', {
        current_password: currentPassword,
        new_password: nextPassword,
      });
    }
    setUser((current) => current ? { ...current, firstLogin: false } : current);
    notify('密码已更新', '请使用新密码登录其他终端。');
  }, [mode, notify]);

  const value = useMemo<AppState>(() => ({
    user, authReady, authError, sessionReason, mode, theme, options,
    selectedProject, selectedYear, selectedVersion, toasts,
    setTheme, setSelectedProject: setProject, setSelectedYear: setYear, setSelectedVersion: setVersion,
    login, logout, changePassword, refreshContext: loadContext,
    clearSessionReason: () => setSessionReason(''), notify,
  }), [user, authReady, authError, sessionReason, mode, theme, options, selectedProject, selectedYear, selectedVersion, toasts, setTheme, login, logout, changePassword, loadContext, notify]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppState {
  const value = useContext(AppContext);
  if (!value) throw new Error('useApp must be used inside AppProvider');
  return value;
}

export function useApiData<T>(path: string, demoValue: T, dependencies: unknown[] = []) {
  const { mode } = useApp();
  const emptyLike = useCallback((value: unknown): unknown => {
    if (Array.isArray(value)) return [];
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, emptyLike(item)]));
    }
    if (typeof value === 'number') return 0;
    if (typeof value === 'boolean') return false;
    return '';
  }, []);
  const [data, setData] = useState<T>(() => mode === 'demo' ? demoValue : emptyLike(demoValue) as T);
  const [loading, setLoading] = useState(mode === 'live');
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    if (mode === 'demo') {
      setData(demoValue);
      setLoading(false);
      setError('');
      return () => { active = false; };
    }
    if (!path) {
      setData(emptyLike(demoValue) as T);
      setLoading(false);
      setError('');
      return () => { active = false; };
    }
    setData(emptyLike(demoValue) as T);
    setLoading(true);
    setError('');
    api.get<T>(path)
      .then((payload) => active && setData(payload))
      .catch((reason) => active && setError(reason instanceof Error ? reason.message : '加载失败'))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
    // Callers pass primitive context values in dependencies.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, path, emptyLike, ...dependencies]);

  return { data, setData, loading, error };
}
