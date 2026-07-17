import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Banknote,
  CheckCircle2,
  CircleDollarSign,
  GripVertical,
  ListChecks,
  Settings2,
  TrendingUp,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api } from '../api';
import { useApiData, useApp } from '../state/AppContext';
import { Button, DataState, EmptyState, Metric, Modal, PageHeader, PriorityBadge, Section, StatusBadge, formatMoney, statusTone } from '../components/UI';

type DashboardComponentKey = 'metrics' | 'status_distribution' | 'budget_distribution' | 'recent_requirements' | 'tasks';

interface RecentRequirement {
  id: string;
  resourceId: string;
  title: string;
  project: string;
  version: string;
  owner: string;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  status: string;
  budget: number | null;
  updatedAt: string;
}

interface DashboardTask {
  title: string;
  detail: string;
  tone: 'danger' | 'warning' | 'info';
  path: string;
}

interface DashboardData {
  metrics: {
    requirements: number;
    completed: number;
    budget: number | null;
    spent: number | null;
    risks: number;
    versions: number;
    artifacts: number;
    planningPool: number;
  };
  trend: { month: string; added: number }[];
  priorityDistribution: { name: string; value: number; color: string }[];
  recentRequirements: RecentRequirement[];
}

interface DashboardLayout {
  role: string;
  component_keys: DashboardComponentKey[];
  updated_by?: number | null;
  updated_at?: string | null;
  is_custom?: boolean;
}

const emptyDashboard: DashboardData = {
  metrics: { requirements: 0, completed: 0, budget: null, spent: null, risks: 0, versions: 0, artifacts: 0, planningPool: 0 },
  trend: [],
  priorityDistribution: [],
  recentRequirements: [],
};

const allComponents: DashboardComponentKey[] = ['metrics', 'status_distribution', 'budget_distribution', 'recent_requirements', 'tasks'];
const componentMeta: Record<DashboardComponentKey, { label: string; description: string }> = {
  metrics: { label: '核心指标', description: '需求、交付、预算和运营风险概览' },
  status_distribution: { label: '需求状态分布', description: '各生命周期状态下的需求数量' },
  budget_distribution: { label: '预算执行或优先级', description: '按角色权限展示资金执行或需求优先级' },
  recent_requirements: { label: '最近需求', description: '当前范围最近更新的需求清单' },
  tasks: { label: '角色工作入口', description: '与当前角色职责匹配的常用工作入口' },
};
const roleLabels: Record<string, string> = {
  admin: '管理员', leader: '咨询负责人', customer: '客户', sales: '销售', manager: '项目经理', developer: '研发人员', operator: '运营人员',
};

function normalizeDashboard(
  payload: unknown,
  projectName: string,
  versionName: (id: string) => string,
): DashboardData {
  const value = (payload && typeof payload === 'object' && 'data' in payload ? (payload as { data: unknown }).data : payload) as Record<string, unknown>;
  if (value && Array.isArray(value.trend)) return value as unknown as DashboardData;
  const metrics = (value?.metrics ?? {}) as Record<string, unknown>;
  const distribution = (value?.status_distribution ?? []) as Array<{ status: string; count: number }>;
  const priorities = (value?.priority_distribution ?? []) as Array<{ priority: string; count: number }>;
  const recent = (value?.recent_requirements ?? []) as Array<Record<string, unknown>>;
  const statusNames: Record<string, string> = {
    draft: '草稿', planning: '规划中', scheduled: '已排期', developing: '研发中', acceptance: '待验收', online: '已上线运维', closed: '已关闭',
    rejected: '已驳回', suspended: '已暂停', cancelled: '已取消', changing: '变更中', returned: '已退回',
  };
  const priorityNames: Record<string, RecentRequirement['priority']> = { urgent: 'P0', high: 'P1', medium: 'P2', low: 'P3' };
  const priorityLabels: Record<string, string> = { urgent: '紧急', high: '高', medium: '中', low: '低' };
  const priorityColors: Record<string, string> = { urgent: 'var(--danger)', high: 'var(--warning)', medium: 'var(--primary)', low: 'var(--muted-foreground)' };
  const completed = distribution.filter((item) => ['online', 'closed'].includes(item.status)).reduce((sum, item) => sum + Number(item.count), 0);
  return {
    metrics: {
      requirements: Number(metrics.requirements ?? 0),
      completed,
      budget: metrics.estimated_budget == null ? null : Number(metrics.estimated_budget),
      spent: metrics.actual_cost == null ? null : Number(metrics.actual_cost),
      risks: Number(metrics.open_operations ?? 0),
      versions: Number(metrics.versions ?? 0),
      artifacts: Number(metrics.artifacts ?? 0),
      planningPool: Number(metrics.planning_pool ?? 0),
    },
    trend: distribution.filter((item) => item.count > 0).map((item) => ({ month: statusNames[item.status] ?? item.status, added: Number(item.count) })),
    priorityDistribution: priorities.filter((item) => item.count > 0).map((item) => ({ name: priorityLabels[item.priority] ?? item.priority, value: Number(item.count), color: priorityColors[item.priority] ?? 'var(--muted-foreground)' })),
    recentRequirements: recent.map((item) => ({
      id: String(item.code ?? item.id),
      resourceId: String(item.id),
      title: String(item.title ?? ''),
      project: projectName,
      version: item.version_id ? versionName(String(item.version_id)) : '待规划',
      owner: item.assignee_id ? `用户 #${item.assignee_id}` : '未分配',
      priority: priorityNames[String(item.priority)] ?? 'P2',
      status: statusNames[String(item.status)] ?? String(item.status ?? ''),
      budget: item.estimated_budget == null ? null : Number(item.estimated_budget),
      updatedAt: item.updated_at ? new Date(String(item.updated_at)).toLocaleString('zh-CN', { hour12: false }) : '',
    })),
  };
}

function roleTasks(role: string, metrics: DashboardData['metrics']): DashboardTask[] {
  const planningTask: DashboardTask = { title: '处理待规划需求', detail: `${metrics.planningPool} 项待分配版本`, tone: 'warning', path: '/requirements?view=planning' };
  const operationTask: DashboardTask = { title: '跟进运营服务工单', detail: `${metrics.risks} 项尚未关闭`, tone: metrics.risks ? 'danger' : 'info', path: '/operations' };
  const tasks: Record<string, DashboardTask[]> = {
    admin: [
      { title: '维护用户与项目权限', detail: '账号、角色及客户项目白名单', tone: 'info', path: '/users' },
      { title: '查看安全审计记录', detail: '登录、权限与资金操作追溯', tone: 'info', path: '/audit' },
      operationTask,
    ],
    leader: [planningTask, { title: '核对资金执行', detail: '预算四级穿透与超支预警', tone: 'warning', path: '/fund-trace' }, operationTask],
    customer: [
      { title: '查看本人需求进展', detail: `${metrics.requirements} 项当前可访问需求`, tone: 'info', path: '/requirements' },
      { title: '查看版本规划', detail: `${metrics.versions} 个当前可访问版本`, tone: 'info', path: '/versions' },
      { title: '提交问题或建议', detail: '进入运营服务反馈池', tone: 'info', path: '/operations' },
    ],
    sales: [
      { title: '查看资金申报进度', detail: '草稿、审批及到位状态', tone: 'warning', path: '/fund-applications' },
      { title: '导出项目进展报表', detail: '按当前项目生成服务端报表', tone: 'info', path: '/reports' },
      planningTask,
    ],
    manager: [
      { title: '检查版本交付范围', detail: `${metrics.versions} 个当前可访问版本`, tone: 'info', path: '/versions' },
      { title: '维护成果物与验收材料', detail: `${metrics.artifacts} 份当前可访问成果物`, tone: 'warning', path: '/deliverables' },
      operationTask,
    ],
    developer: [
      { title: '进入版本需求池', detail: `${metrics.requirements} 项当前范围需求`, tone: 'info', path: '/requirements' },
      { title: '处理优先级任务', detail: '领取任务并更新研发状态', tone: 'warning', path: '/requirements' },
    ],
    operator: [operationTask, { title: '归档运维反馈', detail: '关联原需求并上传运维成果物', tone: 'info', path: '/deliverables?stage=6' }],
  };
  return tasks[role] ?? tasks.customer;
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const { selectedProject, selectedYear, selectedVersion, options, user, notify } = useApp();
  const params = new URLSearchParams();
  if (selectedProject) params.set('project_id', selectedProject);
  if (selectedYear) params.set('annual_plan_id', selectedYear);
  if (selectedVersion) params.set('version_id', selectedVersion);
  const endpoint = selectedProject ? `/api/dashboard?${params.toString()}` : '';
  const dashboardQuery = useApiData<unknown>(endpoint, emptyDashboard, [selectedProject, selectedYear, selectedVersion]);
  const layoutQuery = useApiData<DashboardLayout>(user ? '/api/dashboard-layout' : '', { role: user?.role ?? '', component_keys: [] }, [user?.role]);
  const projectName = options.projects.find((item) => item.id === selectedProject)?.name ?? '当前项目';
  const versionName = (id: string) => options.versions.find((item) => item.id === id)?.name ?? `版本 #${id}`;
  const data = normalizeDashboard(dashboardQuery.data, projectName, versionName);
  const tasks = roleTasks(user?.role ?? 'customer', data.metrics);
  const completion = Math.round((data.metrics.completed / Math.max(data.metrics.requirements, 1)) * 100);
  const canSeeMoney = data.metrics.budget !== null && data.metrics.spent !== null;
  const budget = data.metrics.budget ?? 0;
  const spent = data.metrics.spent ?? 0;
  const execution = Math.round((spent / Math.max(budget, 1)) * 100);
  const secondaryDistribution = canSeeMoney
    ? [
        { name: '已执行', value: Math.min(spent, budget), color: 'var(--success)' },
        { name: '预估余额', value: Math.max(budget - spent, 0), color: 'var(--primary)' },
      ].filter((item) => item.value > 0)
    : data.priorityDistribution;
  const hour = new Date().getHours();
  const greeting = hour < 6 ? '夜深了' : hour < 12 ? '上午好' : hour < 18 ? '下午好' : '晚上好';
  const roleFocus = useMemo(() => ({
    admin: '跨角色进展与权限风险', leader: '规划、资金与交付全局', customer: '需求响应与版本计划', sales: '资金申报与项目进展', manager: '版本交付与投入', developer: '优先级与待办任务', operator: '线上问题与推广维护',
  }[user?.role ?? 'customer']), [user?.role]);

  const [configOpen, setConfigOpen] = useState(false);
  const [configLoading, setConfigLoading] = useState(false);
  const [configSaving, setConfigSaving] = useState(false);
  const [configError, setConfigError] = useState('');
  const [layouts, setLayouts] = useState<DashboardLayout[]>([]);
  const [configRole, setConfigRole] = useState('customer');
  const [draftKeys, setDraftKeys] = useState<DashboardComponentKey[]>([]);
  const [dragKey, setDragKey] = useState<DashboardComponentKey | null>(null);

  async function openConfigurator() {
    setConfigOpen(true);
    setConfigLoading(true);
    setConfigError('');
    try {
      const response = await api.get<DashboardLayout[] | { items: DashboardLayout[] }>('/api/dashboard-layouts');
      const values = Array.isArray(response) ? response : response.items;
      setLayouts(values);
      const initialRole = values.some((item) => item.role === configRole) ? configRole : values[0]?.role ?? 'customer';
      setConfigRole(initialRole);
      setDraftKeys(values.find((item) => item.role === initialRole)?.component_keys ?? []);
    } catch (reason) {
      setConfigError(reason instanceof Error ? reason.message : '看板配置加载失败');
    } finally {
      setConfigLoading(false);
    }
  }

  function selectConfigRole(role: string) {
    setConfigRole(role);
    setDraftKeys(layouts.find((item) => item.role === role)?.component_keys ?? []);
  }

  function toggleComponent(key: DashboardComponentKey, visible: boolean) {
    if (visible) setDraftKeys((current) => current.includes(key) ? current : [...current, key]);
    else setDraftKeys((current) => current.length === 1 ? current : current.filter((item) => item !== key));
  }

  function moveComponent(key: DashboardComponentKey, direction: -1 | 1) {
    setDraftKeys((current) => {
      const index = current.indexOf(key);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function dropComponent(target: DashboardComponentKey) {
    if (!dragKey || dragKey === target || !draftKeys.includes(target)) return;
    setDraftKeys((current) => {
      const next = current.filter((item) => item !== dragKey);
      next.splice(next.indexOf(target), 0, dragKey);
      return next;
    });
    setDragKey(null);
  }

  async function saveLayout() {
    if (!draftKeys.length || configSaving) return;
    setConfigSaving(true);
    try {
      const updated = await api.patch<DashboardLayout>(`/api/dashboard-layouts/${configRole}`, { component_keys: draftKeys });
      setLayouts((current) => current.map((item) => item.role === updated.role ? updated : item));
      if (updated.role === user?.role) layoutQuery.setData(updated);
      notify('角色看板已保存', `${roleLabels[configRole] ?? configRole} 将在下次进入工作台时使用新布局。`);
      setConfigOpen(false);
    } catch (reason) {
      notify('看板配置保存失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setConfigSaving(false);
    }
  }

  function widget(key: DashboardComponentKey) {
    if (key === 'metrics') return <div className="dashboard-widget dashboard-widget--metrics" key={key}>
      <div className="metrics-grid metrics-grid--five">
        <Metric label="版本需求" value={data.metrics.requirements} detail={`已完成 ${data.metrics.completed} 项`} icon={<ListChecks size={19} />} />
        <Metric label="交付完成率" value={`${completion}%`} detail={`已完成 ${data.metrics.completed} 项`} tone="success" icon={<CheckCircle2 size={19} />} />
        {canSeeMoney ? <Metric label="需求预估预算" value={formatMoney(budget)} detail={`实际消耗占比 ${execution}%`} icon={<Banknote size={19} />} /> : <Metric label="落地版本" value={data.metrics.versions} detail="当前可访问范围" icon={<Banknote size={19} />} />}
        {canSeeMoney ? <Metric label="需求实际消耗" value={formatMoney(spent)} detail={`预估余额 ${formatMoney(Math.max(budget - spent, 0))}`} tone="success" icon={<CircleDollarSign size={19} />} /> : <Metric label="归档成果物" value={data.metrics.artifacts} detail="当前可访问范围" tone="success" icon={<CircleDollarSign size={19} />} />}
        <Metric label="待处理风险" value={data.metrics.risks} detail="未关闭运营反馈" tone="warning" icon={<AlertTriangle size={19} />} />
      </div>
    </div>;

    if (key === 'status_distribution') return <Section className="dashboard-widget" key={key} title="需求状态分布" action={<button className="section-link" onClick={() => navigate('/requirements')}>查看需求池<ArrowRight size={16} /></button>}>
      <div className="chart-box" aria-label="需求状态分布图">
        {data.trend.length ? <ResponsiveContainer width="100%" height="100%"><BarChart data={data.trend} margin={{ top: 18, right: 8, left: -20, bottom: 0 }}><CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} /><XAxis dataKey="month" tickLine={false} axisLine={false} tick={{ fill: 'var(--muted-foreground)', fontSize: 12 }} /><YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: 'var(--muted-foreground)', fontSize: 12 }} /><Tooltip contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--foreground)' }} /><Bar dataKey="added" name="需求数" fill="var(--primary)" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer> : <EmptyState title="当前范围暂无需求" />}
      </div>
    </Section>;

    if (key === 'budget_distribution') return <Section className="dashboard-widget" key={key} title={canSeeMoney ? '预算执行' : '需求优先级'} action={canSeeMoney ? <button className="section-link" onClick={() => navigate('/fund-trace')}>四级穿透<ArrowRight size={16} /></button> : undefined}>
      {secondaryDistribution.length ? <div className="donut-layout"><div className="donut-chart"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={secondaryDistribution} dataKey="value" nameKey="name" innerRadius="62%" outerRadius="86%" paddingAngle={2}>{secondaryDistribution.map((entry) => <Cell fill={entry.color} key={entry.name} />)}</Pie><Tooltip formatter={(value) => canSeeMoney ? formatMoney(Number(value)) : Number(value)} contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6 }} /></PieChart></ResponsiveContainer><div className="donut-center"><strong>{canSeeMoney ? formatMoney(budget) : data.metrics.requirements}</strong><span>{canSeeMoney ? '预估预算' : '需求总数'}</span></div></div><div className="budget-legend">{secondaryDistribution.map((item) => <div key={item.name}><i style={{ background: item.color }} /><span>{item.name}</span><strong>{canSeeMoney ? formatMoney(item.value) : item.value}</strong></div>)}</div></div> : <EmptyState title="当前范围暂无数据" />}
    </Section>;

    if (key === 'recent_requirements') return <Section className="dashboard-widget" key={key} title="最近需求" action={<button className="section-link" onClick={() => navigate('/requirements')}>全部需求<ArrowRight size={16} /></button>}>
      {data.recentRequirements.length ? <div className="table-wrap"><table className="data-table"><thead><tr><th>需求</th><th>版本</th><th>优先级</th><th>负责人</th><th>状态</th>{canSeeMoney && <th>预估预算</th>}</tr></thead><tbody>{data.recentRequirements.map((item) => <tr key={item.resourceId} onClick={() => navigate(`/requirements?q=${item.id}`)} tabIndex={0} onKeyDown={(event) => event.key === 'Enter' && navigate(`/requirements?q=${item.id}`)}><td><strong>{item.title}</strong><small>{item.id}{item.updatedAt ? ` · ${item.updatedAt}` : ''}</small></td><td>{item.version}</td><td><PriorityBadge value={item.priority} /></td><td>{item.owner}</td><td><StatusBadge tone={statusTone(item.status)}>{item.status}</StatusBadge></td>{canSeeMoney && <td>{item.budget == null ? '-' : formatMoney(item.budget)}</td>}</tr>)}</tbody></table></div> : <EmptyState title="当前范围暂无需求" />}
    </Section>;

    return <Section className="dashboard-widget" key={key} title="角色工作入口">
      <div className="task-list">{tasks.map((task) => <button key={`${task.path}-${task.title}`} onClick={() => navigate(task.path)}><span className={`task-dot task-dot--${task.tone}`} /><span><strong>{task.title}</strong><small>{task.detail}</small></span><ArrowRight size={17} /></button>)}</div>
      <button className="button button--secondary button--full" onClick={() => navigate(tasks[0]?.path ?? '/requirements')}><TrendingUp size={17} />进入{roleLabels[user?.role ?? 'customer']}工作视图</button>
    </Section>;
  }

  const layoutKeys = layoutQuery.data.component_keys.filter((key): key is DashboardComponentKey => allComponents.includes(key));
  const configuredRows = [...draftKeys, ...allComponents.filter((key) => !draftKeys.includes(key))];

  return <div className="page">
    <PageHeader title={`${user?.name}，${greeting}`} subtitle={`当前视角关注：${roleFocus}`} actions={user?.role === 'admin' ? <Button variant="secondary" onClick={() => void openConfigurator()}><Settings2 size={17} />配置角色看板</Button> : undefined} />
    <DataState loading={dashboardQuery.loading || layoutQuery.loading} error={dashboardQuery.error || layoutQuery.error}>
      {layoutKeys.length ? <div className="dashboard-layout">{layoutKeys.map(widget)}</div> : <EmptyState icon={<Settings2 size={28} />} title="当前角色尚未配置看板组件" />}
    </DataState>
    <Modal
      open={configOpen}
      title="配置角色看板"
      wide
      onClose={() => setConfigOpen(false)}
      footer={<><Button variant="secondary" disabled={configSaving} onClick={() => setConfigOpen(false)}>取消</Button><Button disabled={configSaving || configLoading || Boolean(configError) || !draftKeys.length} onClick={() => void saveLayout()}>{configSaving ? '保存中...' : '保存布局'}</Button></>}
    >
      <DataState loading={configLoading} error={configError}>
        <div className="dashboard-config">
          <label className="field"><span>配置角色</span><select value={configRole} onChange={(event) => selectConfigRole(event.target.value)}>{layouts.map((layout) => <option value={layout.role} key={layout.role}>{roleLabels[layout.role] ?? layout.role}{layout.is_custom ? ' · 已自定义' : ' · 默认'}</option>)}</select></label>
          <div className="dashboard-config-list" aria-label="看板组件排序与显示设置">
            {configuredRows.map((key) => {
              const visible = draftKeys.includes(key);
              const index = draftKeys.indexOf(key);
              return <div
                key={key}
                className={visible ? 'is-visible' : 'is-hidden'}
                draggable={visible}
                onDragStart={() => setDragKey(key)}
                onDragEnd={() => setDragKey(null)}
                onDragOver={(event) => visible && event.preventDefault()}
                onDrop={() => dropComponent(key)}
              >
                <span className="dashboard-config-handle" title={visible ? '拖动排序' : undefined}><GripVertical size={18} /></span>
                <div><strong>{componentMeta[key].label}</strong><small>{componentMeta[key].description}</small></div>
                <label className="dashboard-config-toggle"><input type="checkbox" checked={visible} disabled={visible && draftKeys.length === 1} onChange={(event) => toggleComponent(key, event.target.checked)} /><span>显示</span></label>
                <div className="row-actions">
                  <button className="icon-button" title="上移" aria-label={`上移${componentMeta[key].label}`} disabled={!visible || index <= 0} onClick={() => moveComponent(key, -1)}><ArrowUp size={17} /></button>
                  <button className="icon-button" title="下移" aria-label={`下移${componentMeta[key].label}`} disabled={!visible || index < 0 || index >= draftKeys.length - 1} onClick={() => moveComponent(key, 1)}><ArrowDown size={17} /></button>
                </div>
              </div>;
            })}
          </div>
        </div>
      </DataState>
    </Modal>
  </div>;
}
