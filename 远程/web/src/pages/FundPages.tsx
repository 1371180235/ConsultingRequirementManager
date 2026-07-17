import { AlertTriangle, ArrowRight, Banknote, Check, CheckCircle2, CircleDollarSign, Download, Eye, FileCheck2, FileText, Landmark, Plus, ReceiptText, RefreshCcw, Send, XCircle } from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, apiId, downloadFile, unwrapItems } from '../api';
import { useApiData, useApp } from '../state/AppContext';
import { Button, DataState, EmptyState, Metric, Modal, PageHeader, Section, StatusBadge, formatMoney, statusTone } from '../components/UI';

export interface FundNode { id: string; name: string; level: 'project' | 'year' | 'version' | 'requirement'; budget: number; spent: number; parent_id?: string; status?: string; overrun?: boolean; planningPool?: boolean }
export interface FundTree { nodes: FundNode[] }
type FundEntryType = 'allocation' | 'actual' | 'adjustment';

export function canWriteFundEntries(role: string | undefined): boolean {
  return role === 'admin' || role === 'leader' || role === 'manager';
}

const childLevel: Record<FundNode['level'], FundNode['level'] | null> = { project: 'year', year: 'version', version: 'requirement', requirement: null };
export function childNodes(nodes: FundNode[], parent?: FundNode | null): FundNode[] {
  const expectedLevel = parent ? childLevel[parent.level] : null;
  if (!parent || !expectedLevel) return [];
  return nodes.filter((item) => item.level === expectedLevel && item.parent_id === parent.id);
}
export function normalizeFundTree(payload:FundTree|{data:FundTree}|Record<string,unknown>):FundTree {
  const value=('data'in payload?payload.data:payload) as FundTree|Record<string,unknown>;
  if('nodes'in value && Array.isArray(value.nodes))return value as FundTree;
  const nodes:FundNode[]=[];
  function visit(raw:Record<string,unknown>,parent?:string){const type=String(raw.type??'project');const level:FundNode['level']=type==='annual_plan'?'year':type==='version'?'version':type==='requirement'?'requirement':'project';const item:FundNode={id:String(raw.id),name:String(raw.name??raw.code??''),level,budget:Number(raw.budget??0),spent:Number(raw.actual??0),parent_id:parent,status:raw.status?String(raw.status):undefined,overrun:Boolean(raw.overrun),planningPool:Boolean(raw.planning_pool)};nodes.push(item);for(const child of (raw.children??[]) as Array<Record<string,unknown>>)visit(child,item.id);}
  if ('id' in value) visit(value as Record<string,unknown>);
  return{nodes};
}

function executionRate(node: FundNode): number {
  if (node.budget <= 0) return node.spent > 0 ? 100 : 0;
  return node.spent / node.budget * 100;
}

export function unallocatedBudget(nodes: FundNode[], node: FundNode): number | null {
  if (node.level === 'requirement') return null;
  return node.budget - childNodes(nodes, node).reduce((sum, item) => sum + item.budget, 0);
}

function nodeOverrun(nodes: FundNode[], node: FundNode): boolean {
  const unallocated = unallocatedBudget(nodes, node);
  return Boolean(node.overrun || node.spent > node.budget || (unallocated !== null && unallocated < 0));
}

function nodeWarning(nodes: FundNode[], node: FundNode): boolean {
  return nodeOverrun(nodes, node) || executionRate(node) >= 90;
}

export function FundTracePage() {
  const { selectedProject, options, user, notify } = useApp();
  const [refreshKey, setRefreshKey] = useState(0);
  const endpoint = selectedProject ? `/api/funds/tree?project_id=${encodeURIComponent(selectedProject)}` : '';
  const { data, loading, error } = useApiData<FundTree | { data: FundTree }>(endpoint, { nodes: [] }, [selectedProject, refreshKey]);
  const tree = useMemo(() => normalizeFundTree(data), [data]);
  const versionStatuses = useMemo(() => new Map(options.versions.map((item) => [item.id, item.status])), [options.versions]);
  const nodes = useMemo(() => tree.nodes.map((item) => item.level === 'version' ? { ...item, status: item.status ?? versionStatuses.get(item.id) } : item), [tree.nodes, versionStatuses]);
  const project = nodes.find((item) => item.level === 'project');
  const [yearId, setYearId] = useState('');
  const years = childNodes(nodes, project);
  const activeYear = years.find((item) => item.id === yearId);
  const versions = childNodes(nodes, activeYear);
  const [versionId, setVersionId] = useState('');
  const activeVersion = versions.find((item) => item.id === versionId) ?? versions[0];
  const requirements = childNodes(nodes, activeVersion);
  const [selectedNodeKey, setSelectedNodeKey] = useState('');
  const nodeKey = (node: FundNode) => `${node.level}:${node.id}`;
  const selectedNode = nodes.find((item) => nodeKey(item) === selectedNodeKey) ?? project ?? null;
  const [entryOpen, setEntryOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [entryForm, setEntryForm] = useState({ type: 'allocation' as FundEntryType, yearId: '', versionId: '', requirementId: '', amount: '', description: '', allowOverrun: false });
  useEffect(()=>{const latestYear=years.filter(item=>!item.planningPool).at(-1)??years.at(-1);if(latestYear&&!years.some(item=>item.id===yearId))setYearId(latestYear.id);if(!years.length)setYearId('');},[years,yearId]);
  useEffect(()=>{if(versions.length&&!versions.some(item=>item.id===versionId))setVersionId(versions[0].id);if(!versions.length)setVersionId('');},[versions,versionId]);
  useEffect(()=>{if(project&&!nodes.some(item=>nodeKey(item)===selectedNodeKey))setSelectedNodeKey(nodeKey(project));},[nodes,project,selectedNodeKey]);
  const execution = project ? Math.round(project.spent / Math.max(project.budget, 1) * 100) : 0;
  const chartData = years.map((item) => ({ name: item.name.replace(' 年度',''), budget: item.budget, spent: item.spent }));
  const warningNodes = nodes.filter((item) => nodeWarning(nodes, item));
  const overrunCount = warningNodes.filter((item) => nodeOverrun(nodes, item)).length;
  const projectUnallocated = project ? unallocatedBudget(nodes, project) : null;
  const canWriteEntries = canWriteFundEntries(user?.role);
  const canConfirmOverrun = canWriteEntries;
  function openEntry() {
    if (!canWriteEntries) {
      notify('当前角色不能登记资金流水', '销售账号可查看资金并维护资金申报。', 'warning');
      return;
    }
    const requirement = selectedNode?.level === 'requirement' ? selectedNode : requirements[0];
    const version = requirement ? nodes.find((item) => item.level === 'version' && item.id === requirement.parent_id) : activeVersion;
    const year = version ? nodes.find((item) => item.level === 'year' && item.id === version.parent_id) : activeYear;
    if (!requirement || !version || !year || requirement.planningPool || version.planningPool || year.planningPool) {
      notify('待规划需求暂不能登记资金流水', '请先将需求分配到正式年度和落地版本。', 'warning');
      return;
    }
    setEntryForm({ type:version?.status && version.status !== 'draft' ? 'actual' : 'allocation', yearId:year?.id??yearId, versionId:version?.id??versionId, requirementId:requirement?.id??'', amount:'', description:'', allowOverrun:false });
    setEntryOpen(true);
  }
  async function exportFunds() {
    try { await downloadFile(`/api/exports/funds.csv?project_id=${encodeURIComponent(selectedProject)}`, '资金明细.csv'); }
    catch (reason) { notify('资金明细导出失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger'); }
  }
  async function submitEntry(event: FormEvent) {
    event.preventDefault();
    if (submitting || !canWriteEntries) return;
    setSubmitting(true);
    try {
      await api.post('/api/funds/entries', {
        project_id: apiId(selectedProject), annual_plan_id: apiId(entryForm.yearId), version_id: apiId(entryForm.versionId), requirement_id: apiId(entryForm.requirementId),
        entry_type: entryForm.type, amount: Number(entryForm.amount), description: entryForm.description, allow_actual_overrun: entryForm.type === 'actual' && canConfirmOverrun && entryForm.allowOverrun,
      });
      setRefreshKey((value) => value + 1);
      setEntryOpen(false);
      notify('资金流水已登记', '资金树已按最新数据刷新。');
    } catch (reason) { notify('资金流水登记失败', reason instanceof Error ? reason.message : '请检查层级和金额。', 'danger'); }
    finally { setSubmitting(false); }
  }
  const entryYear = years.find((item) => item.id === entryForm.yearId);
  const entryYears = years.filter((item) => !item.planningPool);
  const entryVersions = childNodes(nodes, entryYear).filter((item) => !item.planningPool);
  const entryVersion = entryVersions.find((item) => item.id === entryForm.versionId);
  const entryRequirements = childNodes(nodes, entryVersion);
  const entryVersionLocked = Boolean(entryVersion?.status && entryVersion.status !== 'draft');
  return <div className="page"><PageHeader title="资金全链路追踪" subtitle="从规划总预算穿透至年度、版本和单项需求" actions={<><Button variant="secondary" onClick={()=>void exportFunds()}><Download size={17}/>导出资金明细</Button>{project&&canWriteEntries&&<Button onClick={openEntry}><ReceiptText size={17}/>登记资金流水</Button>}</>}/><DataState loading={loading} error={error}>{project?<><div className="metrics-grid"><Metric label="规划总预算" value={formatMoney(project.budget)} icon={<Landmark size={19}/>}/><Metric label="实际执行" value={formatMoney(project.spent)} detail={`执行率 ${execution}%`} tone="success" icon={<CircleDollarSign size={19}/>}/><Metric label="下级未分配预算" value={formatMoney(projectUnallocated ?? 0)} detail={`项目预算减年度预算合计 · 执行后余额 ${formatMoney(project.budget - project.spent)}`} tone={(projectUnallocated ?? 0) < 0 ? 'danger' : undefined} icon={<Banknote size={19}/>}/><Metric label="预算预警" value={`${warningNodes.length} 个节点`} detail={overrunCount ? `${overrunCount} 个节点实际超支或下级分配超额` : warningNodes.length ? '执行率达到或超过 90%' : '当前无预警'} tone={overrunCount ? 'danger' : 'warning'} icon={<AlertTriangle size={19}/>}/></div>
      <Section title="四级资金流向" action={<span className="section-note">点击节点查看明细</span>}>
        <div className="fund-flow">
          <div className="fund-level"><span>规划项目</span><button className={selectedNodeKey === nodeKey(project) ? 'fund-node is-active' : 'fund-node'} onClick={() => setSelectedNodeKey(nodeKey(project))}><small>{project.name}</small><strong>{formatMoney(project.budget)}</strong><i><b style={{width: `${Math.min(executionRate(project),100)}%`}}/></i></button></div><ArrowRight className="flow-arrow"/>
          <div className="fund-level"><span>年度计划</span>{years.map((item) => <button key={item.id} className={yearId === item.id ? 'fund-node is-active' : 'fund-node'} onClick={() => {setYearId(item.id); setSelectedNodeKey(nodeKey(item)); const first = childNodes(nodes,item)[0]; setVersionId(first?.id??'');}}><small>{item.name}</small><strong>{formatMoney(item.budget)}</strong><i><b style={{width: `${Math.min(executionRate(item),100)}%`}}/></i></button>)}</div><ArrowRight className="flow-arrow"/>
          <div className="fund-level"><span>落地版本</span>{versions.map((item) => <button key={item.id} className={`${activeVersion?.id === item.id ? 'fund-node is-active' : 'fund-node'}${item.status && item.status !== 'draft' ? ' is-locked' : ''}`} onClick={() => {setVersionId(item.id); setSelectedNodeKey(nodeKey(item));}}><small>{item.name}{item.status && item.status !== 'draft' ? ' · 已冻结' : ''}</small><strong>{formatMoney(item.budget)}</strong><i><b style={{width: `${Math.min(executionRate(item),100)}%`}}/></i></button>)}</div><ArrowRight className="flow-arrow"/>
          <div className="fund-level"><span>需求任务</span>{requirements.map((item) => <button key={item.id} className={`fund-node ${selectedNodeKey === nodeKey(item)?'is-active ':''}${nodeWarning(nodes,item)?'is-warning':''}`} onClick={() => setSelectedNodeKey(nodeKey(item))}><small>{item.name}</small><strong>{formatMoney(item.budget)}</strong><i><b style={{width: `${Math.min(executionRate(item),100)}%`}}/></i></button>)}</div>
        </div>
      </Section>
      <div className="two-column">
        <Section title="年度计划与执行"><div className="chart-box chart-box--compact"><ResponsiveContainer width="100%" height="100%"><BarChart data={chartData} margin={{top:8,right:8,left:-12,bottom:0}}><CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false}/><XAxis dataKey="name" tickLine={false} axisLine={false}/><YAxis tickLine={false} axisLine={false}/><Tooltip formatter={(value) => formatMoney(Number(value))} contentStyle={{background:'var(--surface)',border:'1px solid var(--border)',borderRadius:6}}/><Bar dataKey="budget" name="计划预算" fill="var(--primary)" radius={[3,3,0,0]}/><Bar dataKey="spent" name="实际执行" fill="var(--success)" radius={[3,3,0,0]}/></BarChart></ResponsiveContainer></div></Section>
        <Section title="节点明细">{selectedNode && <div className="fund-detail"><span className="entity-icon"><Landmark size={21}/></span><div><small>{({project:'规划项目',year:'年度计划',version:'落地版本',requirement:'需求任务'} as const)[selectedNode.level]}</small><h3>{selectedNode.name}</h3></div><dl><div><dt>计划 / 分配预算</dt><dd>{formatMoney(selectedNode.budget)}</dd></div><div><dt>实际执行</dt><dd>{formatMoney(selectedNode.spent)}</dd></div><div><dt>执行率</dt><dd>{Math.round(executionRate(selectedNode))}%</dd></div><div><dt>执行后余额</dt><dd>{formatMoney(selectedNode.budget-selectedNode.spent)}</dd></div>{unallocatedBudget(nodes,selectedNode)!==null&&<div><dt>下级未分配预算</dt><dd>{formatMoney(unallocatedBudget(nodes,selectedNode)??0)}</dd></div>}</dl><div className="progress"><i style={{width:`${Math.min(executionRate(selectedNode),100)}%`}}/></div>{canWriteEntries&&selectedNode.level==='requirement'&&!selectedNode.planningPool&&<Button variant="secondary" onClick={openEntry}><ReceiptText size={17}/>登记该需求流水</Button>}</div>}</Section>
      </div>
    </>:<EmptyState icon={<Landmark size={30}/>} title="请先创建并选择规划项目" detail="资金树会在项目、年度、版本和需求建立后自动生成。"/>}</DataState>
    <Modal
      open={entryOpen}
      title="登记资金流水"
      onClose={()=>setEntryOpen(false)}
      footer={<><Button variant="secondary" disabled={submitting} onClick={()=>setEntryOpen(false)}>取消</Button><Button type="submit" form="fund-entry-form" disabled={submitting || !entryForm.requirementId || (entryVersionLocked && entryForm.type !== 'actual')}>{submitting?'登记中...':'确认登记'}</Button></>}
    >
      <form id="fund-entry-form" className="form-grid" onSubmit={event=>void submitEntry(event)}>
        <label className="field field--wide"><span>流水类型</span><select value={entryForm.type} onChange={event=>setEntryForm({...entryForm,type:event.target.value as FundEntryType,allowOverrun:false})}><option value="allocation" disabled={entryVersionLocked}>预算分配</option><option value="actual">实际消耗</option><option value="adjustment" disabled={entryVersionLocked}>预算调整</option></select></label>
        {entryVersionLocked&&<div className="form-alert form-alert--warning field--wide" role="status"><AlertTriangle size={18}/><div><strong>当前版本已冻结</strong><p>基线锁定后不可分配或调整预算，只能继续登记实际消耗。</p></div></div>}
        <label className="field"><span>年度计划</span><select required value={entryForm.yearId} onChange={event=>{const nextYear=entryYears.find(item=>item.id===event.target.value);const nextVersions=childNodes(nodes,nextYear).filter(item=>!item.planningPool);const nextVersion=nextVersions[0];const nextRequirements=childNodes(nodes,nextVersion);const locked=Boolean(nextVersion?.status&&nextVersion.status!=='draft');setEntryForm({...entryForm,type:locked?'actual':entryForm.type,yearId:nextYear?.id??'',versionId:nextVersion?.id??'',requirementId:nextRequirements[0]?.id??'',allowOverrun:false});}}>{entryYears.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field"><span>落地版本</span><select required value={entryForm.versionId} onChange={event=>{const nextVersion=entryVersions.find(item=>item.id===event.target.value);const nextRequirements=childNodes(nodes,nextVersion);const locked=Boolean(nextVersion?.status&&nextVersion.status!=='draft');setEntryForm({...entryForm,type:locked?'actual':entryForm.type,versionId:nextVersion?.id??'',requirementId:nextRequirements[0]?.id??'',allowOverrun:false});}}>{entryVersions.map(item=><option key={item.id} value={item.id}>{item.name}{item.status&&item.status!=='draft'?'（已冻结）':''}</option>)}</select></label>
        <label className="field field--wide"><span>需求任务</span><select required value={entryForm.requirementId} onChange={event=>setEntryForm({...entryForm,requirementId:event.target.value})}>{entryRequirements.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="field field--wide"><span>{entryForm.type==='actual'?'实际消耗金额':entryForm.type==='adjustment'?'调整金额（可为负数）':'分配金额'}（万元）</span><input type="number" step="0.01" min={entryForm.type==='adjustment'?undefined:'0.01'} required value={entryForm.amount} onChange={event=>setEntryForm({...entryForm,amount:event.target.value})}/></label>
        <label className="field field--wide"><span>说明</span><textarea rows={4} maxLength={500} value={entryForm.description} onChange={event=>setEntryForm({...entryForm,description:event.target.value})} placeholder="填写资金依据、用途或调整原因"/></label>
        {entryForm.type==='actual'&&canConfirmOverrun&&<label className="checkbox-row field--wide"><input type="checkbox" checked={entryForm.allowOverrun} onChange={event=>setEntryForm({...entryForm,allowOverrun:event.target.checked})}/><span>如本次登记导致超支，我已确认继续登记</span></label>}
      </form>
    </Modal>
  </div>;
}

interface Application { id: string; title: string; project: string; year: number; annualPlanId?:string; versionId?:string; amount: number; applied: number; owner: string; applicantId?:string; submitted_at: string; status: string; statusCode?:string; reason: string }
export interface ApplicationApi { id:number; project_id:number; annual_plan_id:number; version_id?:number|null; title:string; amount:number|string; status:string; applicant_id:number; applicant_name:string; note?:string; created_at?:string }
const applicationLabels:Record<string,string>={draft:'草稿',submitted:'已提交',reviewing:'审批中',approved:'已通过',rejected:'已驳回',disbursed:'已到位'};
export function applicationFromApi(raw:ApplicationApi,projectName:string,year:number):Application{return{id:String(raw.id),title:raw.title,project:projectName,year,annualPlanId:String(raw.annual_plan_id),versionId:raw.version_id?String(raw.version_id):'',amount:Number(raw.amount),applied:raw.status==='disbursed'?Number(raw.amount):0,owner:raw.applicant_name||'未知申请人',applicantId:String(raw.applicant_id),submitted_at:raw.created_at?new Date(raw.created_at).toLocaleDateString('zh-CN'):'',status:applicationLabels[raw.status]??raw.status,statusCode:raw.status,reason:raw.note??''};}
function applicationItems(payload: Application[]|{items:Application[]}|{data:Application[]},projectName:string,yearForPlan:(id:string)=>number) { const values=Array.isArray(payload)?payload:'items'in payload?unwrapItems(payload):payload.data;const codes:Record<string,string>={'草稿':'draft','已提交':'submitted','审批中':'reviewing','已通过':'approved','已驳回':'rejected','已到位':'disbursed'};return values.map(value=>{const raw=value as Application&Partial<ApplicationApi>;return raw.project?{...value,statusCode:value.statusCode??codes[value.status]}:applicationFromApi(raw as ApplicationApi,projectName,yearForPlan(String(raw.annual_plan_id)));}); }

export function FundApplicationsPage() {
  const {notify,selectedProject,selectedYear,selectedVersion,options,user}=useApp();
  const projectName=options.projects.find(item=>item.id===selectedProject)?.name??'';
  const plans=options.years.filter(item=>item.parentId===selectedProject);
  const yearForPlan=(id:string)=>Number(options.years.find(item=>item.id===id)?.name.match(/\d{4}/)?.[0]??new Date().getFullYear());
  const endpoint=selectedProject?`/api/funds/applications?project_id=${encodeURIComponent(selectedProject)}`:'';
  const {data:raw,setData,loading,error}=useApiData<Application[]|{items:Application[]}|{data:Application[]}>(endpoint,[],[selectedProject]);
  const items=applicationItems(raw,projectName,yearForPlan);
  const [open,setOpen]=useState(false); const [editing,setEditing]=useState<Application|null>(null);const [detail,setDetail]=useState<Application|null>(null);const [submitting,setSubmitting]=useState(false);const [updatingId,setUpdatingId]=useState(''); const [form,setForm]=useState({title:'',annualPlanId:selectedYear,versionId:selectedVersion,amount:'',reason:''});
  const formVersions=options.versions.filter(item=>item.parentId===form.annualPlanId);
  const approved=items.filter(i=>i.statusCode==='disbursed'||i.status==='已到位').reduce((s,i)=>s+i.applied,0);
  const canCreate=['admin','leader','sales'].includes(user?.role??'');const isApplicant=Boolean(detail?.applicantId&&user?.id&&detail.applicantId===user.id);const applicantCanAct=canCreate&&isApplicant;const reviewerCanAct=Boolean(detail&&['admin','leader'].includes(user?.role??'')&&!isApplicant);
  function openEditor(item?:Application){const target=item??null;setEditing(target);setForm({title:target?.title??'',annualPlanId:target?.annualPlanId??selectedYear,versionId:target?.versionId??selectedVersion,amount:target?String(target.amount):'',reason:target?.reason??''});setOpen(true);}
  async function submit(event:FormEvent){event.preventDefault();if(submitting)return;setSubmitting(true);try{const payload={annual_plan_id:apiId(form.annualPlanId),version_id:apiId(form.versionId),title:form.title,amount:Number(form.amount),note:form.reason};const response=editing?await api.patch<ApplicationApi>(`/api/funds/applications/${editing.id}`,payload):await api.post<ApplicationApi>('/api/funds/applications',{project_id:apiId(selectedProject),...payload});const item=applicationFromApi(response,projectName,yearForPlan(String(response.annual_plan_id)));setData(editing?items.map(existing=>existing.id===item.id?item:existing):[item,...items]);setDetail(item);setOpen(false);notify(editing?'资金申报已更新':'资金申报已保存',editing?'修改已写入草稿或驳回单。':'草稿可继续编辑或提交审批。');}catch(reason){notify('资金申报保存失败',reason instanceof Error?reason.message:'请稍后重试。','danger');}finally{setSubmitting(false);}}
  async function updateStatus(item:Application,statusCode:string){if(updatingId)return;setUpdatingId(item.id);try{const response=await api.patch<ApplicationApi>(`/api/funds/applications/${item.id}/status`,{status:statusCode});const updated=applicationFromApi(response,item.project,item.year);setData(items.map(i=>i.id===item.id?updated:i));setDetail(null);notify(`申报状态已更新为${updated.status}`);}catch(reason){notify('申报状态更新失败',reason instanceof Error?reason.message:'请稍后重试。','danger');}finally{setUpdatingId('');}}
  return <div className="page"><PageHeader title="资金申报" subtitle="统一管理年度申报、审批进度和到位金额" actions={canCreate&&selectedProject?<Button onClick={()=>openEditor()}><Plus size={17}/>新建申报</Button>:undefined}/><DataState loading={loading} error={error}><div className="metrics-grid"><Metric label="申报总额" value={formatMoney(items.reduce((s,i)=>s+i.amount,0))} icon={<FileCheck2 size={19}/>}/><Metric label="已批复到位" value={formatMoney(approved)} tone="success" icon={<CheckCircle2 size={19}/>}/><Metric label="审批中" value={`${items.filter(i=>['submitted','reviewing'].includes(i.statusCode??'')).length} 笔`} detail="含已提交和审核中" tone="warning"/><Metric label="未到位" value={formatMoney(items.reduce((s,i)=>s+i.amount,0)-approved)} /></div><Section><div className="table-wrap"><table className="data-table"><thead><tr><th>申报信息</th><th>年度</th><th>申报金额</th><th>到位金额</th><th>申报人</th><th>状态</th><th><span className="sr-only">操作</span></th></tr></thead><tbody>{items.map(item=><tr key={item.id}><td><strong>{item.title}</strong><small>{item.id} · {item.submitted_at}</small></td><td>{item.year}</td><td>{formatMoney(item.amount)}</td><td>{formatMoney(item.applied)}</td><td>{item.owner}</td><td><StatusBadge tone={statusTone(item.status)}>{item.status}</StatusBadge></td><td><button className="icon-button" onClick={()=>setDetail(item)} aria-label={`查看 ${item.id}`}><Eye size={18}/></button></td></tr>)}</tbody></table></div></Section></DataState>
    <Modal open={open} title={editing?'编辑资金申报':'新建资金申报'} onClose={()=>setOpen(false)} footer={<><Button variant="secondary" disabled={submitting} onClick={()=>setOpen(false)}>取消</Button><Button form="fund-form" type="submit" disabled={submitting}>{submitting?'保存中...':editing?'保存修改':'保存草稿'}</Button></>}><form id="fund-form" className="form-grid" onSubmit={e=>void submit(e)}><label className="field field--wide"><span>申报名称</span><input value={form.title} onChange={e=>setForm({...form,title:e.target.value})} required/></label><label className="field"><span>申报年度</span><select required value={form.annualPlanId} onChange={e=>{const nextVersions=options.versions.filter(item=>item.parentId===e.target.value);setForm({...form,annualPlanId:e.target.value,versionId:nextVersions[0]?.id??''});}}>{plans.map(plan=><option key={plan.id} value={plan.id}>{plan.name}</option>)}</select></label><label className="field"><span>落地版本（可选）</span><select value={form.versionId} onChange={e=>setForm({...form,versionId:e.target.value})}><option value="">不关联版本</option>{formVersions.map(version=><option key={version.id} value={version.id}>{version.name}</option>)}</select></label><label className="field field--wide"><span>申报金额（万元）</span><input type="number" min="0.01" step="0.01" value={form.amount} onChange={e=>setForm({...form,amount:e.target.value})} required/></label><label className="field field--wide"><span>申报用途与依据</span><textarea rows={5} value={form.reason} onChange={e=>setForm({...form,reason:e.target.value})} required/></label></form></Modal>
    <Modal open={Boolean(detail)} title={detail?.id??'申报详情'} onClose={()=>setDetail(null)} footer={detail&&<>{applicantCanAct&&['draft','rejected'].includes(detail.statusCode??'')&&<Button variant="secondary" disabled={updatingId===detail.id} onClick={()=>{setDetail(null);openEditor(detail);}}><FileText size={17}/>编辑申报</Button>}{applicantCanAct&&detail.statusCode==='draft'&&<Button disabled={updatingId===detail.id} onClick={()=>void updateStatus(detail,'submitted')}><Send size={17}/>提交审批</Button>}{applicantCanAct&&detail.statusCode==='rejected'&&<Button disabled={updatingId===detail.id} onClick={()=>void updateStatus(detail,'draft')}><RefreshCcw size={17}/>转为草稿</Button>}{reviewerCanAct&&detail.statusCode==='submitted'&&<><Button variant="danger" disabled={updatingId===detail.id} onClick={()=>void updateStatus(detail,'rejected')}><XCircle size={17}/>驳回</Button><Button disabled={updatingId===detail.id} onClick={()=>void updateStatus(detail,'reviewing')}><Check size={17}/>开始审核</Button></>}{reviewerCanAct&&detail.statusCode==='reviewing'&&<><Button variant="danger" disabled={updatingId===detail.id} onClick={()=>void updateStatus(detail,'rejected')}><XCircle size={17}/>驳回</Button><Button disabled={updatingId===detail.id} onClick={()=>void updateStatus(detail,'approved')}><Check size={17}/>批准</Button></>}{reviewerCanAct&&detail.statusCode==='approved'&&<Button disabled={updatingId===detail.id} onClick={()=>void updateStatus(detail,'disbursed')}><CircleDollarSign size={17}/>确认到位</Button>}</>}><div className="application-detail">{detail&&<><div className="detail-heading"><div><small>{detail.project}</small><h3>{detail.title}</h3></div><StatusBadge tone={statusTone(detail.status)}>{detail.status}</StatusBadge></div><dl><div><dt>申报金额</dt><dd>{formatMoney(detail.amount)}</dd></div><div><dt>已到位</dt><dd>{formatMoney(detail.applied)}</dd></div><div><dt>申报人</dt><dd>{detail.owner}</dd></div><div><dt>提交日期</dt><dd>{detail.submitted_at}</dd></div></dl><section><h4><FileText size={17}/>申报用途与依据</h4><p>{detail.reason}</p></section></>}</div></Modal>
  </div>;
}
