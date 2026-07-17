import {
  BarChart3, BriefcaseBusiness, CalendarRange, ChevronDown, ClipboardCheck,
  FileClock, FileSpreadsheet, FileStack, GitCompareArrows, HandCoins, Landmark, LayoutDashboard,
  KeyRound, LogOut, Menu, Milestone, Moon, PanelLeftClose, Search, Settings2, ShieldCheck, Sun,
  Tags, UserCog, Users, Wrench, X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useApp, type ThemeName } from '../state/AppContext';
import type { Role } from '../types';
import { Button, Modal, ToastRegion } from './UI';

const navGroups = [
  { label: '工作', items: [
    { to: '/dashboard', label: '工作台', icon: LayoutDashboard },
    { to: '/search', label: '全局搜索', icon: Search },
    { to: '/reports', label: '报表导出', icon: FileSpreadsheet },
  ] },
  { label: '规划与交付', items: [
    { to: '/projects', label: '规划项目', icon: BriefcaseBusiness },
    { to: '/annual-plans', label: '年度计划', icon: CalendarRange },
    { to: '/versions', label: '落地版本', icon: Tags },
    { to: '/requirements', label: '需求池', icon: ClipboardCheck },
    { to: '/compare', label: '版本比对', icon: GitCompareArrows },
    { to: '/deliverables', label: '阶段成果物', icon: FileStack },
    { to: '/milestones', label: '流程里程碑', icon: Milestone },
  ] },
  { label: '资金与运营', items: [
    { to: '/fund-trace', label: '资金追踪', icon: Landmark, roles: ['admin','leader','sales','manager'] as Role[] },
    { to: '/fund-applications', label: '资金申报', icon: HandCoins, roles: ['admin','leader','sales','manager'] as Role[] },
    { to: '/operations', label: '运营服务', icon: Wrench },
  ] },
  { label: '系统', items: [
    { to: '/users', label: '用户与权限', icon: Users, roles: ['admin'] as Role[] },
    { to: '/audit', label: '审计日志', icon: FileClock, roles: ['admin','leader'] as Role[] },
  ] },
];

const themes: { value: ThemeName; label: string; swatches: string[] }[] = [
  { value: 'blue', label: '专业蓝', swatches: ['#2563eb', '#059669', '#f8fafc'] },
  { value: 'green', label: '清雅绿', swatches: ['#047857', '#2563eb', '#f7faf8'] },
  { value: 'warm', label: '暖灰橙', swatches: ['#c2410c', '#0f766e', '#fafaf9'] },
  { value: 'dark', label: '深色', swatches: ['#60a5fa', '#34d399', '#111827'] },
];

export default function AppShell() {
  const { user, theme, setTheme, options, selectedProject, selectedYear, selectedVersion, setSelectedProject, setSelectedYear, setSelectedVersion, logout, changePassword, notify } = useApp();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [themeOpen, setThemeOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordForm, setPasswordForm] = useState({ current: '', next: '', confirm: '' });
  const location = useLocation();
  const navigate = useNavigate();
  const themeRef = useRef<HTMLDivElement>(null);
  const accountRef = useRef<HTMLDivElement>(null);

  useEffect(() => setMobileOpen(false), [location.pathname]);
  useEffect(() => {
    if (!mobileOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMobileOpen(false);
    };
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [mobileOpen]);
  useEffect(() => {
    document.documentElement.dataset.page = location.pathname;
    return () => { delete document.documentElement.dataset.page; };
  }, [location.pathname]);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const version = options.versions.find((item) => item.id === params.get('version_id'));
    const year = options.years.find((item) => item.id === (params.get('annual_plan_id') ?? version?.parentId));
    const project = options.projects.find((item) => item.id === (params.get('project_id') ?? year?.parentId));
    if (project) setSelectedProject(project.id);
    if (year && (!project || year.parentId === project.id)) setSelectedYear(year.id);
    if (version && (!year || version.parentId === year.id)) setSelectedVersion(version.id);
  }, [location.search, options.projects, options.years, options.versions, setSelectedProject, setSelectedYear, setSelectedVersion]);
  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!themeRef.current?.contains(event.target as Node)) setThemeOpen(false);
      if (!accountRef.current?.contains(event.target as Node)) setAccountOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const years = useMemo(() => options.years.filter((item) => item.parentId === selectedProject), [options.years, selectedProject]);
  const versions = useMemo(() => options.versions.filter((item) => item.parentId === selectedYear), [options.versions, selectedYear]);
  const pageTitle = navGroups.flatMap((group) => group.items).find((item) => item.to === location.pathname)?.label ?? '项目管理中心';
  const passwordStrong = passwordForm.next.length >= 10 && /[a-z]/.test(passwordForm.next) && /[A-Z]/.test(passwordForm.next) && /\d/.test(passwordForm.next) && /[^A-Za-z\d]/.test(passwordForm.next);

  function replaceWorkspaceContext(projectId: string, yearId: string, versionId: string) {
    const params = new URLSearchParams(location.search);
    if (projectId) params.set('project_id', projectId); else params.delete('project_id');
    if (yearId) params.set('annual_plan_id', yearId); else params.delete('annual_plan_id');
    if (versionId) params.set('version_id', versionId); else params.delete('version_id');
    if (versionId) params.delete('planning_pool');
    ['target_id', 'requirement_id', 'artifact_id', 'operation_id'].forEach((key) => params.delete(key));
    navigate({ pathname: location.pathname, search: params.toString() }, { replace: true });
  }

  function selectProject(projectId: string) {
    const nextYear = options.years.find((item) => item.parentId === projectId)?.id ?? '';
    const nextVersion = options.versions.find((item) => item.parentId === nextYear)?.id ?? '';
    setSelectedProject(projectId);
    setSelectedYear(nextYear);
    setSelectedVersion(nextVersion);
    replaceWorkspaceContext(projectId, nextYear, nextVersion);
  }

  function selectYear(yearId: string) {
    const nextVersion = options.versions.find((item) => item.parentId === yearId)?.id ?? '';
    setSelectedYear(yearId);
    setSelectedVersion(nextVersion);
    replaceWorkspaceContext(selectedProject, yearId, nextVersion);
  }

  function selectVersion(versionId: string) {
    setSelectedVersion(versionId);
    replaceWorkspaceContext(selectedProject, selectedYear, versionId);
  }

  async function submitPassword(event: FormEvent) {
    event.preventDefault();
    if (passwordBusy) return;
    if (!passwordStrong) { notify('新密码不符合安全要求', '至少 10 位，并包含大小写字母、数字和特殊字符。', 'warning'); return; }
    if (passwordForm.next !== passwordForm.confirm) { notify('两次输入的新密码不一致', '请重新确认新密码。', 'warning'); return; }
    if (passwordForm.current === passwordForm.next) { notify('新密码不能与当前密码相同', undefined, 'warning'); return; }
    setPasswordBusy(true);
    try {
      await changePassword(passwordForm.current, passwordForm.next);
      setPasswordForm({ current:'', next:'', confirm:'' });
      setPasswordOpen(false);
    } catch (reason) { notify('密码修改失败', reason instanceof Error ? reason.message : '请检查当前密码。', 'danger'); }
    finally { setPasswordBusy(false); }
  }

  return (
    <div className={`app-shell ${collapsed ? 'app-shell--collapsed' : ''}`}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      {mobileOpen && <button className="nav-backdrop" aria-label="关闭导航" onClick={() => setMobileOpen(false)} />}
      <aside className={`sidebar ${mobileOpen ? 'sidebar--open' : ''}`} aria-label="主导航">
        <div className="brand">
          <span className="brand__mark"><BarChart3 size={23} /></span>
          {!collapsed && <div><strong>项目管理中心</strong><span>CRM Workspace</span></div>}
          <button className="icon-button sidebar__mobile-close" onClick={() => setMobileOpen(false)} aria-label="关闭导航"><X size={20} /></button>
        </div>
        <nav className="nav-list">
          {navGroups.map((group)=>({...group,items:group.items.filter((item)=>!('roles' in item)||!item.roles||item.roles.includes(user?.role??'customer'))})).filter(group=>group.items.length).map((group) => (
            <div className="nav-group" key={group.label}>
              {!collapsed && <p>{group.label}</p>}
              {group.items.map(({ to, label, icon: Icon }) => (
                <NavLink key={to} to={to} title={collapsed ? label : undefined} className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}>
                  <Icon size={19} aria-hidden="true" /><span>{label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar__footer">
          <button className="nav-item" onClick={() => void logout()}><LogOut size={19} /><span>退出登录</span></button>
          <button className="sidebar__collapse" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? '展开导航' : '收起导航'}><PanelLeftClose size={18} /><span>{collapsed ? '' : '收起导航'}</span></button>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="topbar__identity">
            <button className="icon-button topbar__menu" onClick={() => setMobileOpen(true)} aria-label="打开导航"><Menu size={21} /></button>
            <div><span>当前页面</span><strong>{pageTitle}</strong></div>
          </div>
          <div className="context-selectors" aria-label="工作上下文">
            <label><span>项目</span><select value={selectedProject} onChange={(event) => selectProject(event.target.value)}>{options.projects.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <label><span>年度</span><select value={selectedYear} onChange={(event) => selectYear(event.target.value)}>{years.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <label><span>版本</span><select value={selectedVersion} onChange={(event) => selectVersion(event.target.value)} disabled={!versions.length}>{versions.length ? versions.map((item) => <option value={item.id} key={item.id}>{item.name}</option>) : <option value="">暂无版本</option>}</select></label>
          </div>
          <div className="topbar__actions">
            <button className="icon-button" onClick={() => navigate('/search')} aria-label="搜索"><Search size={20} /></button>
            <div className="theme-menu" ref={themeRef}>
              <button className="icon-button" aria-label="切换主题" aria-expanded={themeOpen} onClick={() => setThemeOpen((value) => !value)}>{theme === 'dark' ? <Moon size={20} /> : <Sun size={20} />}</button>
              {themeOpen && <div className="theme-popover" role="menu">
                <div className="popover-title"><Settings2 size={17} /><strong>界面主题</strong></div>
                {themes.map((item) => <button key={item.value} role="menuitemradio" aria-checked={theme === item.value} className={theme === item.value ? 'theme-option theme-option--active' : 'theme-option'} onClick={() => { setTheme(item.value); setThemeOpen(false); }}>
                  <span className="theme-swatches">{item.swatches.map((color) => <i key={color} style={{ background: color }} />)}</span><span>{item.label}</span>{theme === item.value && <ShieldCheck size={17} />}
                </button>)}
              </div>}
            </div>
            <div className="account-menu" ref={accountRef}>
              <button className="profile-button" onClick={() => setAccountOpen((value)=>!value)} aria-label="账户菜单" aria-expanded={accountOpen}>
                <span className="avatar">{user?.name.slice(0, 1)}</span>
                <span><strong>{user?.name}</strong><small>{user?.roleLabel}</small></span><ChevronDown size={16} />
              </button>
              {accountOpen&&<div className="account-popover" role="menu"><div className="account-summary"><span className="avatar">{user?.name.slice(0,1)}</span><div><strong>{user?.name}</strong><small>{user?.username}</small></div></div>{user?.role==='admin'&&<button role="menuitem" onClick={()=>{setAccountOpen(false);navigate('/users');}}><UserCog size={17}/><span>用户与权限</span></button>}<button role="menuitem" onClick={()=>{setAccountOpen(false);setPasswordOpen(true);}}><KeyRound size={17}/><span>修改密码</span></button><button role="menuitem" onClick={()=>void logout()}><LogOut size={17}/><span>退出登录</span></button></div>}
            </div>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1}><Outlet /></main>
      </div>
      <Modal open={passwordOpen} title="修改密码" onClose={()=>setPasswordOpen(false)} footer={<><Button variant="secondary" disabled={passwordBusy} onClick={()=>setPasswordOpen(false)}>取消</Button><Button type="submit" form="change-password-form" disabled={passwordBusy||!passwordForm.current||!passwordStrong||passwordForm.next!==passwordForm.confirm}>{passwordBusy?'修改中...':'确认修改'}</Button></>}><form id="change-password-form" className="form-grid" onSubmit={event=>void submitPassword(event)}><label className="field field--wide"><span>当前密码</span><input type="password" autoComplete="current-password" required value={passwordForm.current} onChange={event=>setPasswordForm({...passwordForm,current:event.target.value})}/></label><label className="field field--wide"><span>新密码</span><input type="password" autoComplete="new-password" required minLength={10} value={passwordForm.next} onChange={event=>setPasswordForm({...passwordForm,next:event.target.value})}/><small>至少 10 位，并包含大小写字母、数字和特殊字符。</small></label><label className="field field--wide"><span>确认新密码</span><input type="password" autoComplete="new-password" required minLength={10} value={passwordForm.confirm} onChange={event=>setPasswordForm({...passwordForm,confirm:event.target.value})}/></label></form></Modal>
      <ToastRegion />
    </div>
  );
}
