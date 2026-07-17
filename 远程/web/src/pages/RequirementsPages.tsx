import { ArrowDownToLine, ArrowRight, CheckCircle2, ChevronRight, CircleUserRound, ClipboardList, Clock3, Download, Filter, GitCompareArrows, Hand, History, Link2, List, Plus, Search, Tags, X } from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api, apiId, downloadFile, unwrapItems } from '../api';
import { requirements as demoRequirements } from '../mockData';
import { useApiData, useApp } from '../state/AppContext';
import type { Requirement } from '../types';
import { Button, DataState, EmptyState, Metric, Modal, PageHeader, PriorityBadge, Section, StatusBadge, formatMoney, statusTone } from '../components/UI';

const statusLabels:Record<string,string>={draft:'草稿',planning:'规划中',scheduled:'已排期',developing:'研发中',acceptance:'待验收',online:'已上线运维',closed:'已关闭',rejected:'已驳回',suspended:'已暂停',cancelled:'已取消',changing:'变更中',returned:'已退回'};
const statusCodes=Object.fromEntries(Object.entries(statusLabels).map(([code,label])=>[label,code]));
const statuses=Object.values(statusLabels);
const transitionCodes:Record<string,string[]>={draft:['planning','cancelled'],planning:['scheduled','rejected','suspended','returned'],scheduled:['developing','suspended','cancelled','returned'],developing:['acceptance','suspended','returned'],acceptance:['online','returned'],online:['closed','changing'],closed:['changing'],rejected:['draft','cancelled'],suspended:['planning','scheduled','developing','cancelled'],cancelled:['draft'],changing:['planning','scheduled','developing','acceptance'],returned:['planning','scheduled','developing','cancelled']};
interface TagApi { id:number; name:string; color:string }

function itemArray(payload: Requirement[] | { items: Requirement[] } | { data: Requirement[] }): Requirement[] {
  if (Array.isArray(payload)) return payload;
  if ('items' in payload) return unwrapItems(payload);
  return payload.data;
}

function normalizeRequirements(items:Requirement[],options:ReturnType<typeof useApp>['options']):Requirement[]{
  const priorityLabels:Record<string,Requirement['priority']>={urgent:'P0',high:'P1',medium:'P2',low:'P3'};
  const roleNames:Record<string,string>={admin:'管理员',leader:'咨询负责人',customer:'客户',sales:'销售',manager:'项目经理',developer:'研发人员',operator:'运营人员'};
  return items.map((item)=>{
    const raw=item as Requirement&{code?:string;stable_key?:string;project_id?:number;version_id?:number|null;stakeholder_role?:string;estimated_budget?:number|string;assignee_id?:number|null;tag_ids?:number[];updated_at?:string;priority:string;status:string;source_requirement_id?:number|null;actual_hours?:number|string;requester_id?:number};
    if(raw.resourceId||!raw.code)return item;
    return {id:raw.code,resourceId:String(raw.id),title:raw.title,description:raw.description,project:options.projects.find(entry=>entry.id===String(raw.project_id))?.name??'',version:raw.version_id?options.versions.find(entry=>entry.id===String(raw.version_id))?.name??`#${raw.version_id}`:'待规划',source:roleNames[raw.stakeholder_role??'']??raw.stakeholder_role??'',owner:raw.assignee_id?`#${raw.assignee_id}`:'未分配',priority:priorityLabels[raw.priority]??raw.priority as Requirement['priority'],status:statusLabels[raw.status]??raw.status,budget:Number(raw.estimated_budget??0),tags:(raw.tag_ids??[]).map(id=>options.tags?.find(tag=>tag.id===String(id))?.name??`标签#${id}`),updatedAt:raw.updated_at?new Date(raw.updated_at).toLocaleString('zh-CN',{hour12:false}):'',sourceRequirementId:raw.source_requirement_id?String(raw.source_requirement_id):undefined,actualHours:raw.actual_hours===undefined?undefined:Number(raw.actual_hours),requesterId:raw.requester_id?String(raw.requester_id):undefined,stableKey:raw.stable_key};
  });
}

function downloadCsv(items: Requirement[]) {
  const escape = (value: unknown) => `"${String(value ?? '').replaceAll('"', '""')}"`;
  const rows = [['需求ID', '名称', '版本', '来源', '负责人', '优先级', '状态', '预算(万元)', '标签'], ...items.map((item) => [item.id, item.title, item.version, item.source, item.owner, item.priority, item.status, item.budget, item.tags.join('/')])];
  const blob = new Blob([`\uFEFF${rows.map((row) => row.map(escape).join(',')).join('\r\n')}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `需求清单-${new Date().toISOString().slice(0, 10)}.csv`; anchor.click(); URL.revokeObjectURL(url);
}

export function RequirementsPage() {
  const { mode, notify, selectedProject, selectedYear, selectedVersion, options, user, refreshContext } = useApp();
  const [searchParams] = useSearchParams();
  const requestedQuery = searchParams.get('q') ?? '';
  const targetRequirement = searchParams.get('requirement_id') ?? '';
  const requestedPlanningPool = searchParams.get('planning_pool') === 'true';
  const [query, setQuery] = useState(requestedQuery);
  const [status, setStatus] = useState('');
  const [version, setVersion] = useState(selectedVersion);
  const [planningOnly, setPlanningOnly] = useState(requestedPlanningPool);
  const [view, setView] = useState<'table' | 'board'>('table');
  const [createOpen, setCreateOpen] = useState(false);
  const [detailId, setDetailId] = useState('');
  const [hours, setHours] = useState('');
  const [refreshKey,setRefreshKey]=useState(0);
  const [assignVersion,setAssignVersion]=useState('');
  const [assigning,setAssigning]=useState(false);
  const [transitionNote,setTransitionNote]=useState('');const [transitionTarget,setTransitionTarget]=useState('');
  const [tagManagerOpen,setTagManagerOpen]=useState(false);const [tagItems,setTagItems]=useState<TagApi[]>([]);const [tagLoading,setTagLoading]=useState(false);const [tagError,setTagError]=useState('');const [tagSaving,setTagSaving]=useState(false);const [tagForm,setTagForm]=useState({name:'',color:'#64748B'});
  const [form, setForm] = useState({ title: '', description:'', version: selectedVersion, source: '客户', priority: 'P2' as Requirement['priority'], budget: '', tags: [] as string[], original: '', stableKey:'' });
  const versions = options.versions.filter((item) => item.parentId === selectedYear);
  const projectYearIds=options.years.filter(item=>item.parentId===selectedProject).map(item=>item.id);
  const projectVersions=options.versions.filter(item=>projectYearIds.includes(item.parentId??''));
  useEffect(()=>{setVersion(selectedVersion);setForm(current=>({...current,version:selectedVersion}));},[selectedVersion]);
  const params = new URLSearchParams();
  if(selectedProject)params.set('project_id',selectedProject);
  if (planningOnly) params.set('planning_pool','true');
  else {
    if (selectedYear) params.set('annual_plan_id',selectedYear);
    if (version) params.set('version_id',version);
  }
  const endpoint = selectedProject?`/api/requirements?${params.toString()}`:'';
  const { data: raw, setData, loading, error } = useApiData<Requirement[] | { items: Requirement[] } | { data: Requirement[] }>(endpoint, demoRequirements, [selectedProject,selectedYear,version,planningOnly,refreshKey]);
  const items = normalizeRequirements(mode==='live'&&!selectedProject?[]:itemArray(raw),options);
  const planningEndpoint=selectedProject?`/api/requirements?project_id=${encodeURIComponent(selectedProject)}&planning_pool=true`:'';
  const {data:planningRaw,loading:planningLoading}=useApiData<Requirement[]|{items:Requirement[]}|{data:Requirement[]}>(planningEndpoint,demoRequirements,[selectedProject,refreshKey]);
  const planningCount=mode==='live'&&(planningLoading||!selectedProject)?0:normalizeRequirements(itemArray(planningRaw),options).filter((item)=>item.version==='待规划'||!item.version).length;
  const allProjectEndpoint=selectedProject?`/api/requirements?project_id=${encodeURIComponent(selectedProject)}`:'';
  const {data:allProjectRaw}=useApiData<Requirement[]|{items:Requirement[]}|{data:Requirement[]}>(allProjectEndpoint,demoRequirements,[selectedProject,refreshKey]);
  const allProjectRequirements=normalizeRequirements(mode==='live'&&!selectedProject?[]:itemArray(allProjectRaw),options);
  const stableOptions=allProjectRequirements.filter((item,index,all)=>item.stableKey&&all.findIndex(candidate=>candidate.stableKey===item.stableKey)===index);
  const filtered = useMemo(() => items.filter((item) => {
    const matchesQuery = !query || `${item.id}${item.resourceId??''}${item.title}${item.owner}${item.tags.join('')}`.toLowerCase().includes(query.toLowerCase());
    const matchesStatus = !status || item.status === status;
    const matchesVersion = !version || item.version === version || item.version.includes(versions.find((entry) => entry.id === version)?.name.split(' ')[0] ?? '__none__');
    const matchesPlanning = !planningOnly || item.version === '待规划' || !item.version;
    return matchesQuery && matchesStatus && matchesVersion && matchesPlanning;
  }), [items, query, status, version, planningOnly, versions]);
  const detail = items.find((item) => item.id === detailId);
  const sourceRequirement=detail?.sourceRequirementId?items.find(item=>item.resourceId===detail.sourceRequirementId):undefined;
  const canAssign=Boolean(detail?.version==='待规划'&&['admin','leader','manager'].includes(user?.role??''));
  const canSeeMoney=['admin','leader','sales','manager'].includes(user?.role??'');
  const developerOwnsDetail=Boolean(user?.role==='developer'&&detail&&(detail.owner===`#${user.id}`||detail.owner===user.name));
  const canTransitionDetail=Boolean(detail&&(['admin','leader','manager'].includes(user?.role??'')||developerOwnsDetail));
  const canLogHours=Boolean(detail&&(['admin','leader','manager'].includes(user?.role??'')||developerOwnsDetail));
  const canManageTags=['admin','leader','manager'].includes(user?.role??'');
  useEffect(()=>{if(canAssign&&!projectVersions.some(item=>item.id===assignVersion))setAssignVersion(selectedVersion&&projectVersions.some(item=>item.id===selectedVersion)?selectedVersion:projectVersions[0]?.id??'');},[canAssign,projectVersions,assignVersion,selectedVersion]);
  useEffect(()=>{setQuery(requestedQuery);setPlanningOnly(requestedPlanningPool);},[requestedQuery,requestedPlanningPool]);
  useEffect(()=>{if(!targetRequirement)return;const target=items.find(item=>item.id===targetRequirement||item.resourceId===targetRequirement);if(target)setDetailId(target.id);},[items,targetRequirement]);

  async function openTagManager(){setTagManagerOpen(true);setTagLoading(true);setTagError('');try{setTagItems(await api.get<TagApi[]>('/api/tags'));}catch(reason){setTagError(reason instanceof Error?reason.message:'标签加载失败');}finally{setTagLoading(false);}}
  async function createTag(event:FormEvent){event.preventDefault();if(tagSaving||!tagForm.name.trim())return;setTagSaving(true);try{const created=await api.post<TagApi>('/api/tags',{name:tagForm.name.trim(),color:tagForm.color});setTagItems(current=>[...current,created].sort((left,right)=>left.name.localeCompare(right.name,'zh-CN')));setTagForm({name:'',color:'#64748B'});await refreshContext();notify('标签已创建',`${created.name} 已可在需求表单中选择。`);}catch(reason){notify('标签创建失败',reason instanceof Error?reason.message:'请稍后重试。','danger');}finally{setTagSaving(false);}}

  async function create(event: FormEvent) {
    event.preventDefault();
    const now = new Date();
    const serial = `${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}${String(now.getHours()).padStart(2, '0')}${String(now.getMinutes()).padStart(2, '0')}${String(now.getSeconds()).padStart(2, '0')}`;
    let item: Requirement = { id: `REQ-${now.getFullYear()}-${serial}`, title: form.title, description:form.description, project: options.projects.find((entry) => entry.id === selectedProject)?.name ?? '', version: form.version ? options.versions.find((entry) => entry.id === form.version)?.name.split(' ')[0] ?? form.version : '待规划', source: form.source, owner: '未分配', priority: form.priority, status: '草稿', budget: Number(form.budget || 0), tags: form.tags, updatedAt: now.toLocaleString('zh-CN', { hour12: false }),stableKey:form.stableKey||undefined };
    if (mode === 'live') { const roleCodes:Record<string,string>={'客户':'customer','销售':'sales','项目经理':'manager','研发人员':'developer','运营人员':'operator','咨询负责人':'leader','内部销售':'sales','研发交付':'developer','运营服务':'operator','项目负责人':'leader'};const priorityCodes:Record<string,string>={P0:'urgent',P1:'high',P2:'medium',P3:'low'};const payload:Record<string,unknown>={code:item.id,title:form.title,description:form.description,project_id:apiId(selectedProject),annual_plan_id:apiId(options.versions.find(entry=>entry.id===form.version)?.parentId),version_id:apiId(form.version),stakeholder_role:roleCodes[form.source]??'customer',priority:priorityCodes[form.priority],source_requirement_id:apiId(allProjectRequirements.find(entry=>entry.id===form.original)?.resourceId),tag_ids:form.tags.map(name=>options.tags?.find(tag=>tag.name===name)?.id).filter((id):id is string=>Boolean(id)).map(Number)};if(['admin','leader','manager'].includes(user?.role??'')&&form.stableKey)payload.stable_key=form.stableKey;if(['admin','leader','sales','manager'].includes(user?.role??''))payload.estimated_budget=Number(form.budget||0);const response=await api.post<Record<string,unknown>>('/api/requirements',payload);item=normalizeRequirements([response as unknown as Requirement],options)[0]; }
    setData([item, ...items]); setCreateOpen(false);setRefreshKey(value=>value+1); notify('需求已创建', form.version ? '已归属当前版本。' : '未指定版本，已进入待规划池。');
  }
  async function transition(item: Requirement) {
    const currentCode=statusCodes[item.status]??item.status;const targets=transitionCodes[currentCode]??[];const nextCode=transitionTarget||targets[0];if(!nextCode||!transitionNote.trim())return;const next=statusLabels[nextCode]??nextCode;
    try{if (mode === 'live') await api.post(`/api/requirements/${item.resourceId??item.id}/transition`, { status: nextCode,note:transitionNote.trim() });setData(items.map((entry) => entry.id === item.id ? {...entry, status: next, updatedAt: new Date().toLocaleString('zh-CN', {hour12: false})} : entry));setTransitionNote('');setTransitionTarget(''); notify('需求状态已更新', `${item.status} → ${next}`);}catch(reason){notify('需求状态更新失败',reason instanceof Error?reason.message:'请稍后重试。','danger');}
  }
  async function claim(item: Requirement) { try{if (mode === 'live') await api.post(`/api/requirements/${item.resourceId??item.id}/claim`); setData(items.map((entry) => entry.id === item.id ? {...entry, owner: user?.name ?? entry.owner} : entry)); notify('任务已领取');}catch(reason){notify('任务领取失败',reason instanceof Error?reason.message:'请稍后重试。','danger');} }
  async function logHours(item: Requirement) { if (!Number(hours)) return;try{let actualHours=Number(hours);if (mode === 'live'){const response=await api.patch<Record<string,unknown>>(`/api/requirements/${item.resourceId??item.id}/hours`, { actual_hours: actualHours });actualHours=Number(response.actual_hours??actualHours);}setData(items.map(entry=>entry.id===item.id?{...entry,actualHours}:entry));setHours(''); notify('累计工时已更新', `${actualHours} 小时`);}catch(reason){notify('工时更新失败',reason instanceof Error?reason.message:'请稍后重试。','danger');} }
  async function assignToVersion(item:Requirement){if(!assignVersion||assigning)return;setAssigning(true);try{let updated={...item,version:options.versions.find(entry=>entry.id===assignVersion)?.name??assignVersion};if(mode==='live'){const response=await api.patch<Record<string,unknown>>(`/api/requirements/${item.resourceId??item.id}`,{version_id:apiId(assignVersion)});updated=normalizeRequirements([response as unknown as Requirement],options)[0];}setData(items.map(entry=>entry.id===item.id?updated:entry));setDetailId('');setRefreshKey(value=>value+1);notify('需求已分配到版本',updated.version);}catch(reason){notify('需求分配失败',reason instanceof Error?reason.message:'请稍后重试。','danger');}finally{setAssigning(false);}}
  async function exportRequirements(){if(mode==='demo'){downloadCsv(filtered);return;}try{await downloadFile(`/api/exports/requirements.csv?project_id=${encodeURIComponent(selectedProject)}`,'需求清单.csv');notify('需求清单已导出','导出内容已按当前账号权限处理。');}catch(reason){notify('需求清单导出失败',reason instanceof Error?reason.message:'请稍后重试。','danger');}}

  return <div className="page"><PageHeader title="需求池" subtitle="统一收口、四维归属与六阶段状态流转" actions={<>{canManageTags&&<Button variant="secondary" onClick={()=>void openTagManager()}><Tags size={17}/>管理标签</Button>}<Button variant="secondary" disabled={!selectedProject} onClick={() => void exportRequirements()}><Download size={17}/>导出 CSV</Button><Button disabled={!selectedProject} onClick={() => setCreateOpen(true)}><Plus size={17}/>新建需求</Button></>}/>
    <div className="planning-banner"><div><span className="planning-banner__icon"><ArrowDownToLine size={20}/></span><div><strong>待规划池</strong><p>{planningCount} 项需求尚未确定落地版本</p></div></div><button onClick={() => setPlanningOnly((value) => !value)} className={planningOnly ? 'button button--primary' : 'button button--secondary'}>{planningOnly ? '显示全部' : '仅查看待规划'}</button></div>
    <div className="filter-bar"><label className="search-input"><Search size={18}/><input aria-label="搜索需求" placeholder="搜索 ID、需求、负责人或标签" value={query} onChange={(e) => setQuery(e.target.value)}/>{query && <button onClick={() => setQuery('')} aria-label="清空搜索"><X size={17}/></button>}</label><label className="select-filter"><Filter size={17}/><select aria-label="状态筛选" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">全部状态</option>{statuses.map((value) => <option value={value} key={value}>{value}</option>)}</select></label><label className="select-filter"><Tags size={17}/><select aria-label="版本筛选" value={version} onChange={(e) => setVersion(e.target.value)}><option value="">全部版本</option>{versions.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><div className="segmented" aria-label="视图方式"><button className={view === 'table' ? 'is-active' : ''} onClick={() => setView('table')} aria-label="列表视图"><List size={18}/></button><button className={view === 'board' ? 'is-active' : ''} onClick={() => setView('board')} aria-label="看板视图"><ClipboardList size={18}/></button></div></div>
    <DataState loading={loading} error={error}>{filtered.length ? view === 'table' ? <Section><div className="table-wrap"><table className="data-table requirement-table"><thead><tr><th>需求</th><th>版本</th><th>来源 / 标签</th><th>负责人</th><th>状态</th>{canSeeMoney&&<th>预算</th>}<th><span className="sr-only">操作</span></th></tr></thead><tbody>{filtered.map((item) => <tr key={item.id}><td><button className="cell-title" onClick={() => setDetailId(item.id)}><strong>{item.title}</strong><small><PriorityBadge value={item.priority}/>{item.id} · {item.updatedAt}</small></button></td><td><span className={item.version === '待规划' ? 'version-pill version-pill--planning' : 'version-pill'}>{item.version}</span></td><td><small>{item.source}</small><div className="tag-row">{item.tags.slice(0, 2).map((tag) => <span key={tag}>{tag}</span>)}</div></td><td>{item.owner}</td><td><StatusBadge tone={statusTone(item.status)}>{item.status}</StatusBadge></td>{canSeeMoney&&<td>{formatMoney(item.budget)}</td>}<td><button className="icon-button" onClick={() => setDetailId(item.id)} aria-label={`查看 ${item.id}`}><ChevronRight size={18}/></button></td></tr>)}</tbody></table></div></Section> : <div className="kanban"><div className="kanban__grid">{statuses.map((column) => <section className="kanban-column" key={column}><header><span>{column}</span><strong>{filtered.filter((item) => item.status === column).length}</strong></header><div>{filtered.filter((item) => item.status === column).map((item) => <button className="kanban-card" key={item.id} onClick={() => setDetailId(item.id)}><small>{item.id}<PriorityBadge value={item.priority}/></small><strong>{item.title}</strong><span>{item.owner} · {item.version}</span><div className="tag-row">{item.tags.slice(0, 2).map((tag) => <i key={tag}>{tag}</i>)}</div></button>)}</div></section>)}</div></div> : <EmptyState icon={<Search size={28}/>} title="没有匹配的需求" detail="请调整搜索词或筛选条件。"/>}</DataState>
    <Modal open={createOpen} title="新建需求" wide onClose={() => setCreateOpen(false)} footer={<><Button variant="secondary" onClick={() => setCreateOpen(false)}>取消</Button><Button type="submit" form="requirement-form">提交需求</Button></>}><form id="requirement-form" className="form-grid" onSubmit={(event) => void create(event)}><label className="field field--wide"><span>需求名称</span><input value={form.title} onChange={(e) => setForm({...form, title: e.target.value})} required/></label><label className="field field--wide"><span>需求说明</span><textarea rows={4} value={form.description} onChange={(e)=>setForm({...form,description:e.target.value})} required/></label><label className="field"><span>所属规划</span><select disabled value={selectedProject}><option value={selectedProject}>{options.projects.find((item) => item.id === selectedProject)?.name}</option></select></label><label className="field"><span>落地版本</span><select value={form.version} onChange={(e) => setForm({...form, version: e.target.value})}><option value="">暂不确定（进入待规划池）</option>{versions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{['admin','leader','manager'].includes(user?.role??'')&&<label className="field field--wide"><span>跨版本需求标识（选填）</span><select value={form.stableKey} onChange={e=>setForm({...form,stableKey:e.target.value})}><option value="">新需求，自动使用本次需求编号</option>{stableOptions.map(item=><option value={item.stableKey} key={item.stableKey}>{item.stableKey} · {item.title}</option>)}</select></label>}<label className="field"><span>需求来源 / 对接角色</span><select value={form.source} onChange={(e) => setForm({...form, source: e.target.value})}>{['客户','销售','项目经理','研发人员','运营人员','咨询负责人'].map((value) => <option key={value}>{value}</option>)}</select></label><label className="field"><span>优先级</span><select value={form.priority} onChange={(e) => setForm({...form, priority: e.target.value as Requirement['priority']})}>{['P0','P1','P2','P3'].map((value) => <option key={value}>{value}</option>)}</select></label>{canSeeMoney&&<label className="field"><span>预估预算（万元）</span><input type="number" min="0" step="0.01" value={form.budget} onChange={(e) => setForm({...form, budget: e.target.value})}/></label>}<label className="field field--wide"><span>关联原需求</span><select value={form.original} onChange={(e) => setForm({...form, original: e.target.value})}><option value="">无</option>{allProjectRequirements.map((item) => <option value={item.id} key={item.id}>{item.id} · {item.title}</option>)}</select></label><fieldset className="field field--wide tag-picker"><legend>需求标签（可多选）</legend>{options.tags?.length?options.tags.map((tag) => <label key={tag.id}><input type="checkbox" checked={form.tags.includes(tag.name)} onChange={(e) => setForm({...form, tags: e.target.checked ? [...form.tags, tag.name] : form.tags.filter((item) => item !== tag.name)})}/><span>{tag.name}</span></label>):<small>暂无可用标签</small>}</fieldset></form></Modal>
    <Modal open={tagManagerOpen} title="需求标签管理" wide onClose={()=>setTagManagerOpen(false)} footer={<Button variant="secondary" onClick={()=>setTagManagerOpen(false)}>完成</Button>}>
      <DataState loading={tagLoading} error={tagError}>
        <div className="tag-management">
          <form className="tag-create-form" onSubmit={event=>void createTag(event)}>
            <label className="field"><span>标签名称</span><input maxLength={64} required value={tagForm.name} onChange={event=>setTagForm({...tagForm,name:event.target.value})}/></label>
            <label className="field"><span>标签颜色</span><span className="color-field"><input type="color" value={tagForm.color} onChange={event=>setTagForm({...tagForm,color:event.target.value.toUpperCase()})}/><code>{tagForm.color}</code></span></label>
            <Button disabled={tagSaving||!tagForm.name.trim()}>{tagSaving?'创建中...':'新建标签'}</Button>
          </form>
          <div className="tag-management-list">{tagItems.length?tagItems.map(tag=><div key={tag.id}><i style={{backgroundColor:tag.color}}/><strong>{tag.name}</strong><code>{tag.color}</code></div>):<EmptyState icon={<Tags size={26}/>} title="尚未创建需求标签"/>}</div>
        </div>
      </DataState>
    </Modal>
    <Modal open={Boolean(detail)} title={detail?.id ?? '需求详情'} wide onClose={() => {setDetailId('');setTransitionNote('');setTransitionTarget('')}} footer={detail && <>{user?.role==='developer'&&detail.owner==='未分配'&&['已排期','研发中'].includes(detail.status)&&<Button variant="secondary" onClick={() => void claim(detail)}><Hand size={17}/>领取任务</Button>}{canTransitionDetail&&<Button disabled={!transitionNote.trim()||!(transitionCodes[statusCodes[detail.status]??detail.status]?.length)} onClick={() => void transition(detail)}>确认状态流转<ArrowRight size={17}/></Button>}</>}>
      {detail && <div className="requirement-detail"><div className="detail-heading"><div><div className="tag-row">{detail.tags.map((tag) => <span key={tag}>{tag}</span>)}</div><h3>{detail.title}</h3><p>{detail.description||detail.project}</p></div><StatusBadge tone={statusTone(detail.status)}>{detail.status}</StatusBadge></div><div className="detail-grid"><dl><div><dt>落地版本</dt><dd>{detail.version}</dd></div><div><dt>对接来源</dt><dd>{detail.source}</dd></div><div><dt>当前负责人</dt><dd>{detail.owner}</dd></div>{canSeeMoney&&<div><dt>预算占用</dt><dd>{formatMoney(detail.budget)}</dd></div>}</dl><div className="workflow"><h4><History size={17}/>状态流程</h4><div>{statuses.slice(0,7).map((value, index) => { const active = index <= statuses.slice(0,7).indexOf(detail.status); return <span className={active ? 'is-done' : ''} key={value}><i>{active ? <CheckCircle2 size={14}/> : index + 1}</i><small>{value}</small></span>; })}</div></div></div>{canAssign&&<div className="planning-assignment"><div><strong>分配落地版本</strong><p>分配后系统会自动补齐所属年度，并从待规划池移出。</p></div><select value={assignVersion} onChange={event=>setAssignVersion(event.target.value)}>{projectVersions.map(option=><option value={option.id} key={option.id}>{options.years.find(year=>year.id===option.parentId)?.name} · {option.name}</option>)}</select><Button disabled={!assignVersion||assigning} onClick={()=>void assignToVersion(detail)}>{assigning?'分配中...':'确认分配'}</Button></div>}{canTransitionDetail&&<div className="transition-form"><label className="field"><span>流转至</span><select value={transitionTarget||(transitionCodes[statusCodes[detail.status]??detail.status]?.[0]??'')} onChange={event=>setTransitionTarget(event.target.value)} disabled={!(transitionCodes[statusCodes[detail.status]??detail.status]?.length)}>{(transitionCodes[statusCodes[detail.status]??detail.status]??[]).map(code=><option value={code} key={code}>{statusLabels[code]}</option>)}</select></label><label className="field"><span>流转说明</span><input value={transitionNote} onChange={event=>setTransitionNote(event.target.value)} placeholder="必填，将记入状态历史" required/></label></div>}<div className="detail-panels"><section><h4><Link2 size={17}/>关联与变更</h4><p>稳定标识：{detail.stableKey??detail.id}</p><p>原需求：{sourceRequirement?`${sourceRequirement.id} · ${sourceRequirement.title}`:detail.sourceRequirementId?`需求 #${detail.sourceRequirementId}`:'无'}</p><p>最近变更：{detail.updatedAt} · 当前负责人 {detail.owner}</p></section>{canLogHours&&<section><h4><Clock3 size={17}/>累计工时</h4><div className="inline-form"><label><span className="sr-only">累计实际工时</span><input type="number" min="0" step="0.5" placeholder="累计小时" value={hours} onChange={(e) => setHours(e.target.value)}/></label><Button variant="secondary" onClick={() => void logHours(detail)}>更新累计工时</Button></div><p>当前累计：{detail.actualHours===undefined?'暂无数据':`${detail.actualHours} 小时`}</p></section>}</div></div>}
    </Modal>
  </div>;
}

interface CompareItem {
  id: string;
  title: string;
  change: '新增' | '修改' | '移除';
  left?: string;
  right?: string;
  budgetDelta: number;
  fields?: string[];
  leftRecord?: Record<string, unknown>;
  rightRecord?: Record<string, unknown>;
}
const demoCompare: CompareItem[] = [
  { id: 'REQ-2026-031', title: '多源需求统一收口与去重', change: '新增', right: '新增邮件及工单渠道', budgetDelta: 42 },
  { id: 'REQ-2026-028', title: '资金链路四级穿透查询', change: '修改', left: '三级资金分配', right: '增加到单需求的四级穿透', budgetDelta: 11 },
  { id: 'REQ-2026-017', title: '版本基线冻结与变更审批', change: '新增', right: '完整快照及变更审批', budgetDelta: 31 },
  { id: 'REQ-2026-009', title: '旧版手工导入工具', change: '移除', left: '临时数据迁移工具', budgetDelta: -9 },
];

export function VersionComparePage() {
  const navigate = useNavigate(); const { options, selectedProject, mode, notify, user } = useApp();
  const projectYearIds = options.years.filter((item) => item.parentId === selectedProject).map((item) => item.id);
  const available = options.versions.filter((item) => projectYearIds.includes(item.parentId ?? ''));
  const availableKey = available.map((item) => item.id).join('|');
  const [left, setLeft] = useState(available[1]?.id ?? available[0]?.id ?? ''); const [right, setRight] = useState(available[0]?.id ?? ''); const [tab, setTab] = useState<'requirements' | 'budget'>('requirements');
  const canViewMoney = ['admin','leader','sales','manager'].includes(user?.role??'');
  useEffect(() => {
    const ids = available.map((item) => item.id);
    setLeft((current) => ids.includes(current) ? current : ids[1] ?? ids[0] ?? '');
    setRight((current) => ids.includes(current) ? current : ids[0] ?? '');
  // availableKey is a stable representation of the current project's versions.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedProject, availableKey]);
  const canCompare = available.length >= 2 && left && right && left !== right;
  const endpoint = canCompare ? `/api/versions/compare?left_id=${encodeURIComponent(left)}&right_id=${encodeURIComponent(right)}` : '';
  const { data: payload, loading, error } = useApiData<Record<string,unknown>>(endpoint, { items: demoCompare }, [left, right, canCompare]);
  const normalized = useMemo(() => {
    if (Array.isArray(payload.items)) {
      const items = payload.items as CompareItem[];
      return {
        items,
        summary: (payload.summary as {
          added: number;
          modified: number;
          removed: number;
          budget_delta: number;
        } | undefined) ?? {
          added: items.filter((item) => item.change === '新增').length,
          modified: items.filter((item) => item.change === '修改').length,
          removed: items.filter((item) => item.change === '移除').length,
          budget_delta: items.reduce((sum, item) => sum + item.budgetDelta, 0),
        },
        budget: { left: 400, right: 400 + items.reduce((sum, item) => sum + item.budgetDelta, 0) },
      };
    }

    const requirements = payload.requirements as {
      added?: Array<Record<string, unknown>>;
      removed?: Array<Record<string, unknown>>;
      changed?: Array<{
        code: string;
        stable_key?: string;
        fields?: string[];
        left: Record<string, unknown>;
        right: Record<string, unknown>;
      }>;
    } | undefined;
    const budget = payload.budget as { left?: string | number; right?: string | number; difference?: string | number } | undefined;
    const added: CompareItem[] = (requirements?.added ?? []).map((item) => ({
      id: String(item.code ?? item.id),
      title: String(item.title ?? ''),
      change: '新增' as const,
      right: String(item.description ?? '新增需求'),
      budgetDelta: Number(item.estimated_budget ?? 0),
    }));
    const removed: CompareItem[] = (requirements?.removed ?? []).map((item) => ({
      id: String(item.code ?? item.id),
      title: String(item.title ?? ''),
      change: '移除' as const,
      left: String(item.description ?? '移除需求'),
      budgetDelta: -Number(item.estimated_budget ?? 0),
    }));
    const changed: CompareItem[] = (requirements?.changed ?? []).map((item) => ({
      id: item.code,
      title: String(item.right.title ?? item.left.title ?? ''),
      change: '修改' as const,
      left: String(item.left.description ?? '属性已变更'),
      right: String(item.right.description ?? '属性已变更'),
      budgetDelta: Number(item.right.estimated_budget ?? 0) - Number(item.left.estimated_budget ?? 0),
      fields: item.fields ?? [],
      leftRecord: item.left,
      rightRecord: item.right,
    }));
    const items = [...added, ...changed, ...removed];
    return {
      items,
      summary: {
        added: added.length,
        modified: changed.length,
        removed: removed.length,
        budget_delta: Number(budget?.difference ?? 0),
      },
      budget: { left: Number(budget?.left ?? 0), right: Number(budget?.right ?? 0) },
    };
  }, [payload]);
  const items=normalized.items;const summary=normalized.summary;const budget=normalized.budget;
  const fieldLabels:Record<string,string>={title:'需求名称',description:'需求说明',stakeholder_role:'对接角色',estimated_budget:'预估预算',allocated_budget:'分配预算',priority:'优先级',source_requirement_id:'关联原需求',tag_ids:'标签'};
  function fieldValue(field:string,value:unknown){if(value===null||value===undefined||value==='')return '未设置';if(field.includes('budget'))return formatMoney(Number(value));if(Array.isArray(value))return value.length?value.join('、'):'无';if(typeof value==='object')return JSON.stringify(value);return String(value);}
  async function exportComparison(){if(!canCompare)return;if(mode==='demo'){notify('演示模式不导出服务端数据','请登录正式环境后导出版本差异。','info');return;}try{await downloadFile(`/api/exports/version-comparison.csv?left_id=${encodeURIComponent(left)}&right_id=${encodeURIComponent(right)}`,'版本差异报告.csv');}catch(reason){notify('差异报告导出失败',reason instanceof Error?reason.message:'请稍后重试。','danger');}}
  if (available.length < 2) return <div className="page"><PageHeader title="版本比对" subtitle="对照两个版本的需求基线与资金差异"/><Section><EmptyState icon={<GitCompareArrows size={32}/>} title="至少需要两个版本才能比对" detail="当前项目只有一个可用版本。请先创建第二个版本，再返回进行基线对比。" action={<Button onClick={() => navigate('/versions')}><Plus size={17}/>创建第二个版本</Button>}/></Section></div>;
  return <div className="page"><PageHeader title="版本比对" subtitle="对照两个版本的需求基线与资金差异" actions={<Button variant="secondary" disabled={!canCompare} onClick={()=>void exportComparison()}><Download size={17}/>导出差异报告</Button>}/><div className="compare-controls"><label><span>基准版本</span><select value={left} onChange={(e) => setLeft(e.target.value)}>{available.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><GitCompareArrows size={24}/><label><span>对比版本</span><select value={right} onChange={(e) => setRight(e.target.value)}>{available.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label></div>{left === right ? <Section><EmptyState icon={<GitCompareArrows size={30}/>} title="请选择两个不同版本" detail="基准版本和对比版本不能相同。"/></Section> : <DataState loading={loading && mode === 'live'} error={error}><div className="metrics-grid"><Metric label="新增需求" value={summary.added} tone="success"/><Metric label="修改需求" value={summary.modified} tone="warning"/><Metric label="移除需求" value={summary.removed} tone="danger"/>{canViewMoney&&<Metric label="预算变化" value={`${summary.budget_delta >= 0 ? '+' : ''}${formatMoney(summary.budget_delta)}`}/>}</div><div className="tab-bar"><button onClick={() => setTab('requirements')} className={tab === 'requirements' ? 'is-active' : ''}>需求差异</button>{canViewMoney&&<button onClick={() => setTab('budget')} className={tab === 'budget' ? 'is-active' : ''}>预算差异</button>}</div><Section>{tab === 'requirements' ? items.length?<div className="diff-list">{items.map((item) => <article key={`${item.change}-${item.id}`} className={`diff-row diff-row--${item.change === '新增' ? 'added' : item.change === '移除' ? 'removed' : 'changed'}`}><header><StatusBadge tone={item.change === '新增' ? 'success' : item.change === '移除' ? 'danger' : 'warning'}>{item.change}</StatusBadge><div><strong>{item.title}</strong><small>{item.id}</small></div>{canViewMoney&&<span className={item.budgetDelta >= 0 ? 'money-positive' : 'money-negative'}>{item.budgetDelta >= 0 ? '+' : ''}{formatMoney(item.budgetDelta)}</span>}</header>{item.change === '修改' ? item.fields?.length?<div className="field-diff-list">{item.fields.map(field=><div className="side-diff" key={field}><strong>{fieldLabels[field]??field}</strong><div><small>基准版本</small><p>{fieldValue(field,item.leftRecord?.[field])}</p></div><ArrowRight size={20}/><div><small>对比版本</small><p>{fieldValue(field,item.rightRecord?.[field])}</p></div></div>)}</div>:<div className="side-diff"><div><small>基准版本</small><p>{item.left}</p></div><ArrowRight size={20}/><div><small>对比版本</small><p>{item.right}</p></div></div> : <p>{item.right ?? item.left}</p>}</article>)}</div>:<EmptyState icon={<GitCompareArrows size={28}/>} title="两个版本内容一致" detail="未发现需求新增、修改或移除。"/> : <div className="budget-compare"><div><small>{available.find((item) => item.id === left)?.name}</small><strong>{formatMoney(budget.left)}</strong><span>基线预算</span></div><ArrowRight size={24}/><div><small>{available.find((item) => item.id === right)?.name}</small><strong>{formatMoney(budget.right)}</strong><span>对比预算</span></div></div>}</Section></DataState>}</div>;
}
