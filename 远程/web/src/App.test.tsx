// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, useLocation, useNavigate, type NavigateFunction } from 'react-router-dom';
import App from './App';
import { AppProvider } from './state/AppContext';

let navigate: NavigateFunction | undefined;

function NavigationProbe() {
  navigate = useNavigate();
  const location = useLocation();
  return <output data-testid="location-probe">{location.pathname}{location.search}</output>;
}

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    width: 900, height: 320, top: 0, right: 900, bottom: 320, left: 0, x: 0, y: 0,
    toJSON: () => ({}),
  });
});

afterEach(() => {
  cleanup();
  navigate = undefined;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('application routes and themes', () => {
  it('renders all 15 business routes and switches all four themes', async () => {
    const json = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('/api/auth/me')) return json({ id: 1, username: 'admin', full_name: '测试管理员', role: 'admin', must_change_password: false, csrf_token: 'test-csrf' });
      if (path.includes('/api/context')) return json({
        projects: [{ id: 1, code: 'P-001', name: '测试项目' }],
        annual_plans: [{ id: 10, project_id: 1, year: 2026, name: '2026 年度计划' }],
        versions: [{ id: 100, annual_plan_id: 10, code: 'V1.0', name: '一期' }, { id: 101, annual_plan_id: 10, code: 'V1.1', name: '二期' }],
        tags: [],
      });
      if (path.includes('/api/dashboard-layouts')) return json([{ role: 'admin', component_keys: ['metrics', 'status_distribution', 'budget_distribution', 'recent_requirements', 'tasks'], is_custom: false }]);
      if (path.includes('/api/dashboard-layout')) return json({ role: 'admin', component_keys: ['metrics', 'status_distribution', 'budget_distribution', 'recent_requirements', 'tasks'], is_custom: false });
      if (path.includes('/api/dashboard')) return json({ metrics: { requirements: 0, completed: 0, planning_pool: 0, open_operations: 0, versions: 2, artifacts: 0 }, status_distribution: [], priority_distribution: [], recent_requirements: [] });
      if (path.includes('/api/tags')) return json([]);
      if (path.includes('/api/funds/tree')) return json({ id: 1, type: 'project', name: '测试项目', budget: '0.00', actual: '0.00', children: [] });
      if (path.includes('/api/versions/compare')) return json({ left: { id: 100 }, right: { id: 101 }, requirements: { added: [], removed: [], changed: [], unchanged_count: 0 }, budget: { left: '0.00', right: '0.00', difference: '0.00' } });
      if (path.includes('/api/milestones')) return json({ project: { id: 1, name: '测试项目' }, current_stage: 1, stages: [1,2,3,4,5,6].map((stage) => ({ stage, name: `阶段${stage}`, status: stage === 1 ? 'current' : 'pending', artifact_count: 0 })), reminders: [] });
      return json([]);
    }));
    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <NavigationProbe />
        <AppProvider><App /></AppProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole('link', { name: '跳到主要内容' })).toHaveAttribute('href', '#main-content');
    expect(document.getElementById('main-content')).toHaveAttribute('tabindex', '-1');

    const routes: Array<[string, string | RegExp]> = [
      ['/dashboard', /测试管理员，/],
      ['/projects', '规划项目'],
      ['/annual-plans', '年度计划'],
      ['/versions', '落地版本'],
      ['/requirements', '需求池'],
      ['/compare', '版本比对'],
      ['/fund-trace', '资金全链路追踪'],
      ['/fund-applications', '资金申报'],
      ['/deliverables', '阶段里程碑与成果物'],
      ['/milestones', '流程里程碑'],
      ['/operations', '运营服务'],
      ['/search', '全局搜索'],
      ['/reports', '报表导出'],
      ['/users', '用户与权限'],
      ['/audit', '审计日志'],
    ];

    for (const [path, heading] of routes) {
      await act(async () => navigate?.(path));
      expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument();
    }

    await act(async () => navigate?.('/dashboard'));
    const themes = [
      ['blue', '专业蓝'], ['green', '清雅绿'], ['warm', '暖灰橙'], ['dark', '深色'],
    ];
    for (const [value, label] of themes) {
      fireEvent.click(screen.getByRole('button', { name: '切换主题' }));
      fireEvent.click(screen.getByRole('menuitemradio', { name: label }));
      expect(document.documentElement.dataset.theme).toBe(value);
    }
  });

  it('shows a server error without offering a demo-mode login bypass', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('service offline')));
    render(<MemoryRouter initialEntries={['/dashboard']}><AppProvider><App /></AppProvider></MemoryRouter>);
    expect(await screen.findByRole('button', { name: '登录工作台' })).toBeInTheDocument();
    expect(await screen.findByText('服务暂不可用')).toBeInTheDocument();
    expect(screen.queryByText(/演示模式/)).not.toBeInTheDocument();
  });

  it('redirects an empty workspace to projects while keeping user administration available', async () => {
    const json = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('/api/auth/me')) return json({ id: 1, username: 'admin', full_name: '测试管理员', role: 'admin', must_change_password: false, csrf_token: 'test-csrf' });
      if (path.includes('/api/context')) return json({ projects: [], annual_plans: [], versions: [], tags: [] });
      if (path.includes('/api/users')) return json([]);
      if (path.includes('/api/audit')) return json([]);
      return json([]);
    }));
    render(
      <MemoryRouter initialEntries={['/fund-trace']}>
        <NavigationProbe />
        <AppProvider><App /></AppProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { level: 1, name: '规划项目' })).toBeInTheDocument();
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/projects');

    await act(async () => navigate?.('/users'));
    expect(await screen.findByRole('heading', { level: 1, name: '用户与权限' })).toBeInTheDocument();
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/users');
  });

  it('keeps the four theme definitions and responsive viewport contracts', async () => {
    // @ts-expect-error Node types are intentionally not part of the browser bundle.
    const { readFile } = await import('node:fs/promises');
    // @ts-expect-error Node types are intentionally not part of the browser bundle.
    const { join } = await import('node:path');
    const cwd = (globalThis as unknown as { process: { cwd: () => string } }).process.cwd();
    const styles = await readFile(join(cwd, 'src', 'styles.css'), 'utf8');
    for (const theme of ['green', 'warm', 'dark']) {
      expect(styles).toContain(`:root[data-theme="${theme}"]`);
    }
    expect(styles).toContain('@media (max-width: 1024px)');
    expect(styles).toContain('@media (max-width: 768px)');
    expect(styles).toContain('@media (max-width: 480px)');
    expect(styles).toContain('min-width: 320px');
    expect(styles).toContain('overflow-x: hidden');
    expect(styles).toContain('.skip-link:focus { transform:translateY(0); }');
    expect(styles).toContain('--muted-foreground: #5b687a;');

    const tabletStart = styles.indexOf('@media (max-width: 1024px)');
    const mobileStart = styles.indexOf('@media (max-width: 768px)');
    const narrowStart = styles.indexOf('@media (max-width: 480px)');
    const tabletRules = styles.slice(tabletStart, mobileStart);
    const mobileRules = styles.slice(mobileStart, narrowStart);
    const narrowRules = styles.slice(narrowStart);
    expect(tabletRules).toContain('.workspace,.app-shell--collapsed .workspace { width:100%; min-width:0; max-width:100%; margin-left:0; transition:none; }');
    expect(tabletRules).toContain('visibility:hidden; pointer-events:none');
    expect(tabletRules).toContain('visibility:visible; pointer-events:auto');
    expect(tabletRules).toContain('.icon-button,.field-icon-button { width:44px; min-width:44px; }');
    expect(tabletRules).toContain('.button,.section-link,.text-button { min-height:44px; }');
    expect(mobileRules).toContain('.fund-flow { width:100%; min-width:0; max-width:100%; grid-template-columns:minmax(0,1fr);');
    expect(mobileRules).toContain('grid-template-columns:minmax(0,1.25fr) minmax(0,.8fr) minmax(0,1fr)');
    expect(mobileRules).toContain('.nav-item,.sidebar__collapse,.profile-button,.theme-option,.account-popover button');
    expect(mobileRules).toContain('min-height:44px');
    expect(narrowRules).toContain('grid-template-columns:repeat(2,minmax(0,1fr))');
    expect(narrowRules).toContain('white-space:normal');
  });

  it('opens and closes the compact navigation without leaving the page scroll-locked', async () => {
    const json = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('/api/auth/me')) return json({ id: 1, username: 'admin', full_name: '测试管理员', role: 'admin', csrf_token: 'test-csrf' });
      if (path.includes('/api/context')) return json({ projects: [], annual_plans: [], versions: [], tags: [] });
      if (path === '/api/dashboard-layout') return json({ role: 'admin', component_keys: ['metrics', 'tasks'], is_custom: false });
      if (path.startsWith('/api/dashboard?')) return json({ metrics: {}, status_distribution: [], priority_distribution: [], recent_requirements: [] });
      return json([]);
    }));

    render(<MemoryRouter initialEntries={['/projects']}><AppProvider><App /></AppProvider></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '打开导航' }));
    expect(screen.getByRole('complementary', { name: '主导航' })).toHaveClass('sidebar--open');
    expect(screen.getAllByRole('button', { name: '关闭导航' })).toHaveLength(2);
    expect(document.body.style.overflow).toBe('hidden');

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByRole('complementary', { name: '主导航' })).not.toHaveClass('sidebar--open');
    expect(screen.getAllByRole('button', { name: '关闭导航' })).toHaveLength(1);
    expect(document.body.style.overflow).toBe('');
  });

  it('lets an administrator reorder and persist a role dashboard', async () => {
    const savedLayouts: Array<{ path: string; body: { component_keys: string[] } }> = [];
    const json = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const context = {
      projects: [{ id: 1, code: 'P-001', name: '测试项目' }],
      annual_plans: [{ id: 10, project_id: 1, year: 2026, name: '2026 年度计划' }],
      versions: [{ id: 100, annual_plan_id: 10, code: 'V1.0', name: '一期', status: 'draft' }],
      tags: [],
    };
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.includes('/api/auth/me')) return json({ id: 1, username: 'admin', full_name: '测试管理员', role: 'admin', csrf_token: 'test-csrf' });
      if (path.includes('/api/context')) return json(context);
      if (path === '/api/dashboard-layouts/customer' && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { component_keys: string[] };
        savedLayouts.push({ path, body });
        return json({ role: 'customer', component_keys: body.component_keys, is_custom: true });
      }
      if (path === '/api/dashboard-layouts') return json([
        { role: 'admin', component_keys: ['metrics', 'status_distribution', 'budget_distribution', 'recent_requirements', 'tasks'], is_custom: false },
        { role: 'customer', component_keys: ['metrics', 'recent_requirements', 'tasks'], is_custom: false },
      ]);
      if (path === '/api/dashboard-layout') return json({ role: 'admin', component_keys: ['metrics', 'status_distribution', 'budget_distribution', 'recent_requirements', 'tasks'], is_custom: false });
      if (path.startsWith('/api/dashboard?')) return json({ metrics: { requirements: 0, planning_pool: 0, open_operations: 0, versions: 1, artifacts: 0 }, status_distribution: [], priority_distribution: [], recent_requirements: [] });
      return json([]);
    }));

    render(<MemoryRouter initialEntries={['/dashboard']}><AppProvider><App /></AppProvider></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '配置角色看板' }));
    const moveDown = await screen.findByRole('button', { name: '下移核心指标' });
    expect(moveDown).toBeEnabled();
    fireEvent.click(moveDown);
    fireEvent.click(screen.getByRole('button', { name: '保存布局' }));

    expect(await screen.findByText('角色看板已保存')).toBeInTheDocument();
    expect(savedLayouts).toEqual([{ path: '/api/dashboard-layouts/customer', body: { component_keys: ['recent_requirements', 'metrics', 'tasks'] } }]);
  });

  it('does not expose dashboard configuration to non-administrators', async () => {
    const json = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('/api/auth/me')) return json({ id: 8, username: 'customer', full_name: '客户用户', role: 'customer', csrf_token: 'test-csrf' });
      if (path.includes('/api/context')) return json({ projects: [{ id: 1, name: '测试项目' }], annual_plans: [], versions: [], tags: [] });
      if (path === '/api/dashboard-layout') return json({ role: 'customer', component_keys: ['metrics', 'tasks'], is_custom: false });
      if (path.startsWith('/api/dashboard?')) return json({ metrics: { requirements: 0, planning_pool: 0, open_operations: 0, versions: 0, artifacts: 0 }, status_distribution: [], priority_distribution: [], recent_requirements: [] });
      return json([]);
    }));

    render(<MemoryRouter initialEntries={['/dashboard']}><AppProvider><App /></AppProvider></MemoryRouter>);
    expect(await screen.findByRole('heading', { level: 1, name: /客户用户，/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '配置角色看板' })).not.toBeInTheDocument();
  });

  it('creates a requirement tag and refreshes workspace context', async () => {
    let contextLoads = 0;
    let createdBody: { name: string; color: string } | undefined;
    const json = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.includes('/api/auth/me')) return json({ id: 1, username: 'admin', full_name: '测试管理员', role: 'admin', csrf_token: 'test-csrf' });
      if (path.includes('/api/context')) {
        contextLoads += 1;
        return json({ projects: [{ id: 1, name: '测试项目' }], annual_plans: [{ id: 10, project_id: 1, year: 2026, name: '2026 年度计划' }], versions: [{ id: 100, annual_plan_id: 10, code: 'V1.0', name: '一期' }], tags: contextLoads > 1 ? [{ id: 3, name: '合规要求' }] : [] });
      }
      if (path === '/api/tags' && init?.method === 'POST') {
        createdBody = JSON.parse(String(init.body)) as { name: string; color: string };
        return json({ id: 3, ...createdBody }, 201);
      }
      if (path === '/api/tags') return json([]);
      if (path.startsWith('/api/requirements')) return json([]);
      return json([]);
    }));

    render(<MemoryRouter initialEntries={['/requirements']}><AppProvider><App /></AppProvider></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: '管理标签' }));
    fireEvent.change(await screen.findByLabelText('标签名称'), { target: { value: '合规要求' } });
    fireEvent.click(screen.getByRole('button', { name: '新建标签' }));

    expect(await screen.findByText('标签已创建')).toBeInTheDocument();
    expect(createdBody).toEqual({ name: '合规要求', color: '#64748B' });
    expect(contextLoads).toBeGreaterThanOrEqual(2);
  });

  it('opens a global-search requirement in its exact project, year and version context', async () => {
    const requests: string[] = [];
    const json = (value: unknown) => Promise.resolve(new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    const requirement = {
      id: 501,
      code: 'REQ-REMOTE-501',
      stable_key: 'REMOTE-501',
      title: '跨项目定位需求',
      description: '必须切换到远端项目上下文后打开。',
      project_id: 2,
      annual_plan_id: 20,
      version_id: 200,
      requester_id: 1,
      stakeholder_role: 'customer',
      assignee_id: null,
      priority: 'high',
      status: 'developing',
      estimated_budget: '18.00',
      tag_ids: [],
      updated_at: '2026-07-16T08:00:00',
    };
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      requests.push(path);
      if (path.includes('/api/auth/me')) return json({ id: 1, username: 'admin', full_name: '测试管理员', role: 'admin', csrf_token: 'test-csrf' });
      if (path.includes('/api/context')) return json({
        projects: [{ id: 1, name: '默认项目' }, { id: 2, name: '远端项目' }],
        annual_plans: [{ id: 10, project_id: 1, year: 2026, name: '默认年度' }, { id: 20, project_id: 2, year: 2027, name: '远端年度' }],
        versions: [{ id: 100, annual_plan_id: 10, code: 'V1.0', name: '默认版本' }, { id: 200, annual_plan_id: 20, code: 'V2.0', name: '远端版本' }],
        tags: [],
      });
      if (path.startsWith('/api/search?')) return json({ results: [{
        type: 'requirement', id: 501, code: 'REQ-REMOTE-501', title: '跨项目定位需求', project_id: 2,
        annual_plan_id: 20, version_id: 200, version_name: '远端版本', status: 'developing', estimated_budget: '18.00',
      }] });
      if (path.startsWith('/api/requirements?') && path.includes('project_id=2')) return json([requirement]);
      if (path.startsWith('/api/requirements?')) return json([]);
      return json([]);
    }));

    render(
      <MemoryRouter initialEntries={['/search']}>
        <NavigationProbe />
        <AppProvider><App /></AppProvider>
      </MemoryRouter>,
    );

    const search = await screen.findByRole('textbox', { name: '全局搜索' });
    fireEvent.change(search, { target: { value: '跨项目定位' } });
    fireEvent.submit(search.closest('form')!);
    fireEvent.click(await screen.findByRole('button', { name: /跨项目定位需求/ }));

    expect(await screen.findByRole('heading', { level: 1, name: '需求池' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '项目' })).toHaveValue('2');
    expect(screen.getByRole('combobox', { name: '年度' })).toHaveValue('20');
    expect(screen.getByRole('combobox', { name: '版本' })).toHaveValue('200');
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/requirements?project_id=2&annual_plan_id=20&version_id=200&q=REQ-REMOTE-501&requirement_id=REQ-REMOTE-501');
    expect(await screen.findByRole('dialog', { name: 'REQ-REMOTE-501' })).toHaveTextContent('跨项目定位需求');
    expect(requests.some((path) => path.includes('/api/requirements?project_id=2&annual_plan_id=20&version_id=200'))).toBe(true);
  });
});
