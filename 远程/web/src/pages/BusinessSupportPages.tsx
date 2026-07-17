import {
  AlertTriangle, CheckCircle2, Clock3, Download, FileArchive, FileSpreadsheet,
  FileText, Flag, LoaderCircle, Milestone, RefreshCcw,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { api, downloadFile } from '../api';
import { useApiData, useApp } from '../state/AppContext';
import { Button, DataState, EmptyState, Metric, PageHeader, Section, StatusBadge } from '../components/UI';

interface MilestoneStage { stage:number; name:string; status:'completed'|'current'|'pending'; artifact_count:number }
interface MilestoneData {
  project:{id:number|string;name:string}; current_stage:number; stages:MilestoneStage[];
  reminders:{version_id:number|string;version_name:string;type:string;message:string}[];
}
const demoMilestones:MilestoneData={project:{id:'',name:''},current_stage:0,stages:[],reminders:[]};
const stageOutputs=['可研报告','分年任务申报书','任务书方案、需求清单','招标文件、应标文件','验收报告、项目总结','运维反馈、推广维护记录'];

export function MilestonesPage(){
  const {notify,selectedProject,user}=useApp();
  const query=useApiData<MilestoneData|{data:MilestoneData}>(selectedProject?`/api/milestones?project_id=${selectedProject}`:'',demoMilestones,[selectedProject]);
  const data='data'in query.data?query.data.data:query.data;const [selected,setSelected]=useState(data.current_stage);const activeSelected=data.stages.some(stage=>stage.stage===selected)?selected:data.current_stage;
  const current=data.stages.find(stage=>stage.stage===data.current_stage);const completed=data.stages.filter(stage=>stage.status==='completed').length;
  async function advance(){if(data.current_stage>=6)return;const next=data.current_stage+1;try{await api.patch(`/api/projects/${selectedProject}`,{current_stage:next});const updated:MilestoneData={...data,current_stage:next,stages:data.stages.map(stage=>({...stage,status:stage.stage<next?'completed':stage.stage===next?'current':'pending'}))};query.setData(updated);setSelected(next);notify('项目阶段已推进',`当前进入：${updated.stages.find(stage=>stage.stage===next)?.name}`);}catch(reason){notify('项目阶段推进失败',reason instanceof Error?reason.message:'请稍后重试。','danger');}}
  const canAdvance=['admin','leader'].includes(user?.role??'');
  return <div className="page"><PageHeader title="流程里程碑" subtitle="按六个业务阶段掌握当前进展、成果物和验收提醒" actions={canAdvance&&data.current_stage>0&&data.current_stage<6?<Button onClick={()=>void advance()}><Flag size={17}/>推进至下一阶段</Button>:undefined}/><DataState loading={query.loading} error={query.error}><div className="metrics-grid"><Metric label="当前阶段" value={`${data.current_stage} / 6`} detail={current?.name} icon={<Milestone size={19}/>}/><Metric label="已完成阶段" value={completed} tone="success"/><Metric label="已归档成果" value={data.stages.reduce((sum,stage)=>sum+stage.artifact_count,0)} icon={<FileArchive size={19}/>}/><Metric label="待处理提醒" value={data.reminders.length} tone={data.reminders.length?'warning':'success'} icon={<AlertTriangle size={19}/>}/></div><div className="milestone-track" aria-label="六阶段项目流程">{data.stages.map(stage=><button key={stage.stage} className={`${activeSelected===stage.stage?'is-active ':''}${stage.status==='completed'?'is-done ':''}`} onClick={()=>setSelected(stage.stage)}><i>{stage.status==='completed'?<CheckCircle2 size={17}/>:stage.stage}</i><span><small>阶段 {stage.stage}</small><strong>{stage.name}</strong></span></button>)}</div><div className="milestone-page-grid"><Section title="阶段明细"><div className="process-stage-list">{data.stages.map(stage=><button className={activeSelected===stage.stage?'is-active':''} key={stage.stage} onClick={()=>setSelected(stage.stage)}><i>{stage.stage}</i><div><strong>{stage.name}</strong><p>{stageOutputs[stage.stage-1]}</p></div><span>{stage.artifact_count} 份成果</span><StatusBadge tone={stage.status==='completed'?'success':stage.status==='current'?'warning':'neutral'}>{stage.status==='completed'?'已完成':stage.status==='current'?'进行中':'待开始'}</StatusBadge></button>)}</div></Section><Section title="当前提醒">{data.reminders.length?<div className="reminder-list">{data.reminders.map(reminder=><div key={`${reminder.version_id}-${reminder.type}`}><AlertTriangle size={20}/><div><strong>{reminder.version_name}</strong><p>{reminder.message}</p></div></div>)}</div>:<EmptyState icon={<CheckCircle2 size={28}/>} title="当前无待处理提醒"/>}</Section></div></DataState></div>;
}

interface ReportDefinition { id:string; title:string; description:string; icon:'project'|'requirement'|'fund'|'delivery'; money?:boolean; path:(context:{project:string;left:string;right:string})=>string }
const reports:ReportDefinition[]=[
  {id:'project-progress',title:'项目全景进展报表',description:'项目下年度、版本、需求状态、优先级与负责人明细',icon:'project',path:context=>`/api/exports/project-progress.csv?project_id=${context.project}`},
  {id:'requirements',title:'需求全量台账',description:'需求归属、状态、优先级与按权限展示的资金投入',icon:'requirement',path:context=>`/api/exports/requirements.csv?project_id=${context.project}`},
  {id:'version-comparison',title:'版本差异报告',description:'版本间新增、修改、移除需求及变更字段',icon:'requirement',path:context=>`/api/exports/version-comparison.csv?left_id=${context.left}&right_id=${context.right}`},
  {id:'funds',title:'资金计划与执行',description:'总预算、年度、版本和需求的四级资金明细',icon:'fund',money:true,path:context=>`/api/exports/funds.csv?project_id=${context.project}`},
  {id:'funding-applications',title:'资金申报进度报表',description:'申报金额、审批状态、关联范围与申请人',icon:'fund',money:true,path:context=>`/api/exports/funding-applications.csv?project_id=${context.project}`},
  {id:'artifacts',title:'成果物交付清单',description:'六阶段成果物、关联范围、审批状态和附件信息',icon:'delivery',path:context=>`/api/exports/artifacts.csv?project_id=${context.project}`},
  {id:'operations',title:'运营服务复盘报表',description:'工单类型、状态、影响版本与原需求关联',icon:'delivery',path:context=>`/api/exports/operations.csv?project_id=${context.project}`},
];

export function ReportsPage(){
  const {notify,selectedProject,selectedVersion,options,user}=useApp();const [downloading,setDownloading]=useState('');const [history,setHistory]=useState<{name:string;time:string}[]>([]);
  const projectYears=options.years.filter(item=>item.parentId===selectedProject).map(item=>item.id);const available=options.versions.filter(item=>projectYears.includes(item.parentId??''));const left=selectedVersion||available[0]?.id||'';const right=available.find(item=>item.id!==left)?.id||'';
  const visibleReports=useMemo(()=>reports.filter(report=>!report.money||['admin','leader','sales','manager'].includes(user?.role??'')),[user?.role]);
  const icon=(type:ReportDefinition['icon'])=>type==='project'?<Milestone size={21}/>:type==='requirement'?<FileText size={21}/>:type==='fund'?<FileSpreadsheet size={21}/>:<FileArchive size={21}/>;
  async function download(report:ReportDefinition){if(report.id==='version-comparison'&&!right){notify('无法导出版本差异','当前项目至少需要两个版本。','warning');return;}setDownloading(report.id);try{const path=report.path({project:selectedProject,left,right});await downloadFile(path,`${report.id}-${new Date().toISOString().slice(0,10)}.csv`);setHistory(current=>[{name:report.title,time:new Date().toLocaleString('zh-CN',{hour12:false})},...current].slice(0,5));notify('报表已导出',report.title);}catch(reason){notify('报表导出失败',reason instanceof Error?reason.message:'请稍后重试。','danger');}finally{setDownloading('');}}
  return <div className="page"><PageHeader title="报表导出" subtitle="按当前项目、年度和版本上下文生成可追溯业务报表"/><div className="report-toolbar"><div><FileSpreadsheet size={20}/><span><strong>导出范围</strong><small>{options.projects.find(item=>item.id===selectedProject)?.name??'当前项目'}</small></span></div><span className="context-chip">服务端即时生成</span></div><div className="report-layout"><div className="report-grid">{visibleReports.map(report=><article className="report-card" key={report.id}><span className="entity-icon">{icon(report.icon)}</span><div><h2>{report.title}</h2><p>{report.description}</p></div><footer><span><Clock3 size={14}/>根据当前数据生成</span><Button variant="secondary" disabled={Boolean(downloading)} onClick={()=>void download(report)}>{downloading===report.id?<LoaderCircle className="spin" size={17}/>:<Download size={17}/>}CSV</Button></footer></article>)}</div><Section title="最近导出"><div className="export-history">{history.length?history.map((item,index)=><div key={`${item.time}-${index}`}><span className="file-icon"><FileSpreadsheet size={18}/></span><div><strong>{item.name}</strong><small>{item.time}</small></div><CheckCircle2 size={17}/></div>):<EmptyState icon={<RefreshCcw size={25}/>} title="尚无导出记录" detail="本次登录期间的导出结果将显示在这里。"/>}</div></Section></div></div>;
}
