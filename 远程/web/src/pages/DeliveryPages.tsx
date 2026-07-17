import {
  Archive,
  CheckCircle2,
  CircleDot,
  Download,
  Eye,
  ExternalLink,
  FilePenLine,
  FilePlus2,
  FileText,
  Flag,
  GitPullRequestArrow,
  LockKeyhole,
  Megaphone,
  MessageSquareText,
  Plus,
  Rocket,
  Search,
  Send,
  ServerCog,
  Trash2,
  Upload,
  Wrench,
  XCircle,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, downloadFile, unwrapItems } from '../api';
import { useApiData, useApp } from '../state/AppContext';
import { Button, DataState, EmptyState, Metric, Modal, PageHeader, Section, StatusBadge, statusTone } from '../components/UI';

type ApprovalStatus = 'draft' | 'submitted' | 'approved' | 'rejected';

interface StageFile {
  id: string;
  name: string;
  size: string;
  updated: string;
  hasFile?: boolean;
  approvalStatus: ApprovalStatus;
  uploadedBy?: string;
  reviewNote?: string;
}

interface Stage {
  id: string;
  index: number;
  name: string;
  output: string;
  owner: string;
  status: string;
  progress: number;
  files: StageFile[];
}

interface ArtifactApi {
  id: number;
  project_id?: number;
  annual_plan_id?: number | null;
  version_id?: number | null;
  requirement_id?: number | null;
  stage: number;
  title: string;
  original_filename?: string | null;
  size_bytes?: number | null;
  has_file?: boolean;
  approval_status?: ApprovalStatus;
  uploaded_by?: number;
  review_note?: string | null;
  created_at?: string;
}

interface RequirementOption {
  id: number;
  code?: string;
  title?: string;
}

type ArtifactChangeStatus = 'pending' | 'approved' | 'rejected' | 'applied' | 'cancelled';

interface ArtifactChangeOperationApi {
  action?: string;
  artifact_id?: number;
  upload_token?: string;
  data?: {
    stage?: number;
    category?: string;
    title?: string;
    requirement_id?: number | null;
    upload_token?: string;
  };
}

interface StagedArtifactApi {
  token: string;
  version_id?: number;
  change_request_id?: number;
  stage?: number;
  category?: string;
  title?: string;
  requirement_id?: number | null;
  original_filename?: string;
  content_type?: string;
  size_bytes?: number;
  uploaded_by?: number;
  created_at?: string;
}

interface ArtifactChangeRequestApi {
  id: number;
  version_id?: number;
  title?: string;
  reason?: string;
  change_type?: string;
  payload?: { artifacts?: ArtifactChangeOperationApi[] };
  staged_artifacts?: StagedArtifactApi[];
  status?: ArtifactChangeStatus;
  requested_by?: number | string;
  applicant_name?: string;
  requester_name?: string;
  decision_note?: string | null;
  created_at?: string;
}

interface ArtifactChangeUploadResponse {
  change_request: ArtifactChangeRequestApi;
  staged_artifact: StagedArtifactApi;
}

interface ArtifactChangeForm {
  changeTitle: string;
  reason: string;
  artifactTitle: string;
  category: string;
  requirementId: string;
}

const stageTemplates: Array<Omit<Stage, 'status' | 'progress' | 'files'>> = [
  { id: 'stage-1', index: 1, name: '宏观规划', output: '可研报告', owner: '项目负责人' },
  { id: 'stage-2', index: 2, name: '规划细化', output: '分年任务申报书', owner: '内部销售' },
  { id: 'stage-3', index: 3, name: '建设落地', output: '任务书方案、需求清单', owner: '项目经理' },
  { id: 'stage-4', index: 4, name: '招投标', output: '招标文件、应标文件', owner: '内部销售' },
  { id: 'stage-5', index: 5, name: '项目交付验收', output: '验收报告、项目总结', owner: '项目经理' },
  { id: 'stage-6', index: 6, name: '运维运营', output: '运维反馈、推广维护记录', owner: '运营服务' },
];

const approvalLabels: Record<ApprovalStatus, string> = {
  draft: '待提交',
  submitted: '审批中',
  approved: '已通过',
  rejected: '已驳回',
};

const artifactChangeStatusLabels: Record<ArtifactChangeStatus, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
  applied: '已执行',
  cancelled: '已取消',
};

const stageNames = Object.fromEntries(stageTemplates.map((stage) => [stage.index, stage.name])) as Record<number, string>;

export function isArtifactStageLocked(stage: number | undefined, versionStatus: string | undefined): boolean {
  return Boolean(stage && stage >= 3 && stage <= 6 && versionStatus && versionStatus !== 'draft');
}

export function isArtifactChangeRequest(item: ArtifactChangeRequestApi): boolean {
  return Boolean(item.payload?.artifacts?.length || item.staged_artifacts?.length || item.change_type?.startsWith('artifact'));
}

function formatFileSize(value?: number): string {
  if (!value) return '未知大小';
  if (value < 1024 * 1024) return `${Math.max(Math.round(value / 1024), 1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function artifactChangeTone(status: ArtifactChangeStatus): 'success' | 'warning' | 'danger' | 'info' | 'neutral' {
  if (status === 'applied') return 'success';
  if (status === 'pending') return 'warning';
  if (status === 'approved') return 'info';
  if (status === 'rejected') return 'danger';
  return 'neutral';
}

function artifactChangeSummary(item: ArtifactChangeRequestApi, artifacts: ArtifactApi[]): string {
  const operation = item.payload?.artifacts?.[0];
  const staged = item.staged_artifacts?.[0];
  const stage = staged?.stage ?? operation?.data?.stage;
  const stageLabel = stage ? stageNames[stage] ?? `阶段 ${stage}` : '当前版本';
  if (operation?.action === 'replace_file') {
    const target = artifacts.find((artifact) => artifact.id === operation.artifact_id);
    return `替换附件 · ${target?.title || target?.original_filename || `成果物 #${operation.artifact_id ?? '-'}`}`;
  }
  if (operation?.action === 'add') {
    return `新增成果物 · ${stageLabel} · ${operation.data?.title || staged?.title || '未命名成果物'}`;
  }
  return `成果物变更 · ${stageLabel}`;
}

function artifactFile(item: ArtifactApi): StageFile {
  return {
    id: String(item.id),
    name: item.original_filename || item.title,
    size: item.size_bytes ? `${Math.max(item.size_bytes / 1024 / 1024, 0.1).toFixed(1)} MB` : '无附件',
    updated: item.created_at ? new Date(item.created_at).toLocaleDateString('zh-CN') : '-',
    hasFile: item.has_file ?? Boolean(item.original_filename),
    approvalStatus: item.approval_status ?? 'draft',
    uploadedBy: item.uploaded_by ? String(item.uploaded_by) : undefined,
    reviewNote: item.review_note ?? undefined,
  };
}

function stagesFromArtifacts(artifacts: ArtifactApi[]): Stage[] {
  return stageTemplates.map((template) => {
    const files = artifacts.filter((item) => item.stage === template.index).map(artifactFile);
    const approved = files.filter((file) => file.approvalStatus === 'approved').length;
    const submitted = files.some((file) => file.approvalStatus === 'submitted');
    const rejected = files.some((file) => file.approvalStatus === 'rejected');
    return {
      ...template,
      files,
      status: files.length === 0 ? '待归档' : rejected ? '有驳回' : submitted ? '审批中' : approved === files.length ? '已审批' : '待提交',
      progress: files.length ? Math.round((approved / files.length) * 100) : 0,
    };
  });
}

export function DeliverablesPage() {
  const [searchParams] = useSearchParams();
  const targetArtifact = searchParams.get('artifact_id') ?? '';
  const { mode, notify, selectedProject, selectedYear, selectedVersion, options, user } = useApp();
  const [artifacts, setArtifacts] = useState<ArtifactApi[]>([]);
  const [requirements, setRequirements] = useState<RequirementOption[]>([]);
  const [artifactChanges, setArtifactChanges] = useState<ArtifactChangeRequestApi[]>([]);
  const [loading, setLoading] = useState(mode === 'live');
  const [changesLoading, setChangesLoading] = useState(false);
  const [error, setError] = useState('');
  const [changesError, setChangesError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [changeRefreshKey, setChangeRefreshKey] = useState(0);

  useEffect(() => {
    let current = true;
    if (mode !== 'live' || !selectedProject) {
      setArtifacts([]);
      setRequirements([]);
      setLoading(false);
      setError('');
      return () => { current = false; };
    }

    setLoading(true);
    setError('');
    const project = encodeURIComponent(selectedProject);
    const year = encodeURIComponent(selectedYear);
    const version = encodeURIComponent(selectedVersion);
    const artifactRequests: Promise<ArtifactApi[]>[] = [api.get(`/api/artifacts?project_id=${project}&stage=1`)];
    if (selectedYear) artifactRequests.push(api.get(`/api/artifacts?project_id=${project}&annual_plan_id=${year}&stage=2`));
    if (selectedVersion) artifactRequests.push(api.get(`/api/artifacts?project_id=${project}&version_id=${version}`));
    const requirementPath = selectedVersion
      ? `/api/requirements?project_id=${project}${selectedYear ? `&annual_plan_id=${year}` : ''}&version_id=${version}`
      : '';
    const requirementRequest = requirementPath ? api.get<RequirementOption[]>(requirementPath) : Promise.resolve([]);
    const operationRequest = api.get<ArtifactApi[]>(`/api/artifacts?project_id=${project}&stage=6`);

    Promise.all([Promise.all(artifactRequests), requirementRequest, operationRequest])
      .then(([groups, requirementItems, operationItems]) => {
        if (!current) return;
        const requirementIds = new Set(requirementItems.map((item) => item.id));
        const visibleOperations = operationItems.filter((item) => item.requirement_id && requirementIds.has(item.requirement_id));
        const unique = [...groups.flat(), ...visibleOperations].filter(
          (item, index, items) => items.findIndex((candidate) => candidate.id === item.id) === index,
        );
        setRequirements(requirementItems);
        setArtifacts(unique);
      })
      .catch((reason) => current && setError(reason instanceof Error ? reason.message : '成果物加载失败'))
      .finally(() => current && setLoading(false));
    return () => { current = false; };
  }, [mode, selectedProject, selectedYear, selectedVersion, refreshKey]);

  useEffect(() => {
    let current = true;
    if (mode !== 'live' || !selectedVersion) {
      setArtifactChanges([]);
      setChangesLoading(false);
      setChangesError('');
      return () => { current = false; };
    }

    setChangesLoading(true);
    setChangesError('');
    api.get<ArtifactChangeRequestApi[] | { items: ArtifactChangeRequestApi[] }>(
      `/api/change-requests?version_id=${encodeURIComponent(selectedVersion)}`,
    )
      .then((payload) => current && setArtifactChanges(unwrapItems(payload).filter(isArtifactChangeRequest)))
      .catch((reason) => current && setChangesError(reason instanceof Error ? reason.message : '成果物附件变更加载失败'))
      .finally(() => current && setChangesLoading(false));
    return () => { current = false; };
  }, [mode, selectedVersion, changeRefreshKey]);

  const requestedStage = Number(searchParams.get('stage'));
  const stages = stagesFromArtifacts(artifacts);
  const [selected, setSelected] = useState(
    Number.isInteger(requestedStage) && requestedStage >= 1 && requestedStage <= 6 ? `stage-${requestedStage}` : 'stage-1',
  );
  const [requirementId, setRequirementId] = useState('');
  const [uploading, setUploading] = useState(false);
  const [downloadingId, setDownloadingId] = useState('');
  const [actionId, setActionId] = useState('');
  const [decisionFile, setDecisionFile] = useState<StageFile | null>(null);
  const [decisionApproved, setDecisionApproved] = useState(true);
  const [decisionNote, setDecisionNote] = useState('');
  const [deleteFile, setDeleteFile] = useState<StageFile | null>(null);
  const [changeModalOpen, setChangeModalOpen] = useState(false);
  const [changeArtifact, setChangeArtifact] = useState<StageFile | null>(null);
  const [changeFile, setChangeFile] = useState<File | null>(null);
  const [changeForm, setChangeForm] = useState<ArtifactChangeForm>({
    changeTitle: '', reason: '', artifactTitle: '', category: '', requirementId: '',
  });
  const [submittingChange, setSubmittingChange] = useState(false);
  const [changeActionId, setChangeActionId] = useState('');
  const [previewingToken, setPreviewingToken] = useState('');
  const [changeDecision, setChangeDecision] = useState<{ item: ArtifactChangeRequestApi; approved: boolean } | null>(null);
  const [changeDecisionNote, setChangeDecisionNote] = useState('');
  const [cancelChange, setCancelChange] = useState<ArtifactChangeRequestApi | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const changeFileInput = useRef<HTMLInputElement>(null);
  const active = stages.find((stage) => stage.id === selected) ?? stages[0];
  const canUpload = ['admin', 'leader', 'manager', 'sales', 'operator'].includes(user?.role ?? '');
  const canReview = ['admin', 'leader', 'manager'].includes(user?.role ?? '');
  const canManageChanges = ['admin', 'leader'].includes(user?.role ?? '');
  const canPreviewChanges = Boolean(user && user.role !== 'customer');
  const selectedVersionStatus = options.versions.find((item) => item.id === selectedVersion)?.status;
  const activeVersionLocked = isArtifactStageLocked(active?.index, selectedVersionStatus);
  const scopeReady = Boolean(
    active && selectedProject &&
    (active.index === 1 || (active.index === 2 && selectedYear) || (active.index >= 3 && active.index <= 5 && selectedYear && selectedVersion) || (active.index === 6 && requirementId)),
  );

  useEffect(() => {
    if (Number.isInteger(requestedStage) && requestedStage >= 1 && requestedStage <= 6) setSelected(`stage-${requestedStage}`);
  }, [requestedStage]);

  useEffect(() => {
    if (requirements.length && !requirements.some((item) => String(item.id) === requirementId)) setRequirementId(String(requirements[0].id));
    if (!requirements.length) setRequirementId('');
  }, [requirements, requirementId]);

  function refreshArtifacts() {
    setRefreshKey((value) => value + 1);
  }

  function refreshArtifactChanges() {
    setChangeRefreshKey((value) => value + 1);
  }

  function openArtifactChange(target: StageFile | null = null) {
    if (!active || !selectedVersion || !activeVersionLocked) return;
    setChangeArtifact(target);
    setChangeFile(null);
    setChangeForm({
      changeTitle: target ? `替换成果物附件：${target.name}` : `${active.name}新增成果物附件`,
      reason: '',
      artifactTitle: target?.name ?? '',
      category: active.output,
      requirementId,
    });
    if (changeFileInput.current) changeFileInput.current.value = '';
    setChangeModalOpen(true);
  }

  function startUpload() {
    if (activeVersionLocked) openArtifactChange();
    else fileInput.current?.click();
  }

  async function submitArtifactChange(event: FormEvent) {
    event.preventDefault();
    if (!active || !selectedVersion || !changeFile || submittingChange) return;
    if (!changeArtifact && active.index === 6 && !changeForm.requirementId) {
      notify('请选择关联需求', '运维成果物必须关联当前版本内的需求。', 'warning');
      return;
    }
    setSubmittingChange(true);
    try {
      const form = new FormData();
      form.append('file', changeFile);
      form.append('change_title', changeForm.changeTitle.trim());
      form.append('reason', changeForm.reason.trim());
      if (changeArtifact) {
        form.append('artifact_id', changeArtifact.id);
      } else {
        form.append('artifact_title', changeForm.artifactTitle.trim());
        form.append('stage', String(active.index));
        form.append('category', changeForm.category.trim());
        if (active.index === 6) form.append('requirement_id', changeForm.requirementId);
      }
      const response = await api.post<ArtifactChangeUploadResponse>(
        `/api/versions/${encodeURIComponent(selectedVersion)}/artifact-change-requests/upload`,
        form,
      );
      const created = {
        ...response.change_request,
        staged_artifacts: [response.staged_artifact],
        requested_by: response.change_request.requested_by ?? user?.id,
        applicant_name: response.change_request.applicant_name ?? user?.name,
        created_at: response.change_request.created_at ?? new Date().toISOString(),
      };
      setArtifactChanges((items) => [created, ...items.filter((item) => item.id !== created.id)]);
      setChangeModalOpen(false);
      notify(
        changeArtifact ? '附件替换申请已提交' : '成果物新增申请已提交',
        '文件已安全暂存，审批并执行后才会写入冻结版本。',
      );
    } catch (reason) {
      notify('附件变更申请提交失败', reason instanceof Error ? reason.message : '请检查文件与申请内容。', 'danger');
    } finally {
      setSubmittingChange(false);
    }
  }

  async function previewStagedArtifact(item: StagedArtifactApi) {
    if (!item.token || previewingToken) return;
    setPreviewingToken(item.token);
    try {
      await downloadFile(
        `/api/artifact-change-uploads/${encodeURIComponent(item.token)}/download`,
        item.original_filename || item.title || '暂存附件',
      );
    } catch (reason) {
      notify('暂存附件预览失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setPreviewingToken('');
    }
  }

  async function decideArtifactChange() {
    if (!canManageChanges || !changeDecision || !changeDecisionNote.trim() || changeActionId) return;
    const id = String(changeDecision.item.id);
    setChangeActionId(id);
    try {
      await api.patch(`/api/change-requests/${encodeURIComponent(id)}`, {
        approved: changeDecision.approved,
        note: changeDecisionNote.trim(),
      });
      setChangeDecision(null);
      setChangeDecisionNote('');
      refreshArtifactChanges();
      notify(changeDecision.approved ? '附件变更申请已批准' : '附件变更申请已驳回');
    } catch (reason) {
      notify('附件变更审批失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setChangeActionId('');
    }
  }

  async function applyArtifactChange(item: ArtifactChangeRequestApi) {
    const id = String(item.id);
    if (!canManageChanges || changeActionId) return;
    setChangeActionId(id);
    try {
      await api.post(`/api/change-requests/${encodeURIComponent(id)}/apply`);
      refreshArtifacts();
      refreshArtifactChanges();
      notify('附件变更已执行', '正式成果物与版本基线已同步更新。');
    } catch (reason) {
      notify('附件变更执行失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setChangeActionId('');
    }
  }

  async function cancelArtifactChange() {
    if (!cancelChange || changeActionId) return;
    const id = String(cancelChange.id);
    setChangeActionId(id);
    try {
      await api.post(`/api/change-requests/${encodeURIComponent(id)}/cancel`);
      setCancelChange(null);
      refreshArtifactChanges();
      notify('附件变更申请已取消', '暂存文件已清理，正式成果物未发生变化。');
    } catch (reason) {
      notify('取消附件变更失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setChangeActionId('');
    }
  }

  async function upload(files: FileList | null) {
    if (!files?.length || !active || uploading || !scopeReady) return;
    const file = files[0];
    setUploading(true);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('project_id', selectedProject);
      form.append('stage', String(active.index));
      form.append('category', active.output);
      form.append('title', file.name);
      if (active.index >= 2 && active.index <= 5) form.append('annual_plan_id', selectedYear);
      if (active.index >= 3 && active.index <= 5) form.append('version_id', selectedVersion);
      if (active.index === 6) form.append('requirement_id', requirementId);
      await api.post<ArtifactApi>('/api/artifacts/upload', form);
      refreshArtifacts();
      notify('成果物已上传', `${file.name} 已保存为待提交状态。`);
    } catch (reason) {
      notify('成果物上传失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = '';
    }
  }

  async function download(file: StageFile) {
    if (!/^\d+$/.test(file.id) || file.hasFile === false) {
      notify('该成果物暂无可下载附件', '请确认成果物已上传文件。', 'warning');
      return;
    }
    setDownloadingId(file.id);
    try {
      const response = await fetch(`/api/artifacts/${encodeURIComponent(file.id)}/download`, { credentials: 'include' });
      if (!response.ok) {
        const payload = await response.json().catch(() => null) as { detail?: string | { message?: string } } | null;
        const detail = payload?.detail;
        const message = typeof detail === 'string' ? detail : detail?.message ?? `下载请求失败（${response.status}）`;
        if (response.status === 401) window.dispatchEvent(new CustomEvent('crm:session-expired', { detail: message }));
        throw new Error(message);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = file.name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (reason) {
      notify('成果物下载失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setDownloadingId('');
    }
  }

  async function submitArtifact(file: StageFile) {
    if (!/^\d+$/.test(file.id) || actionId) return;
    setActionId(file.id);
    try {
      await api.post(`/api/artifacts/${file.id}/submit`);
      refreshArtifacts();
      notify('成果物已提交审批', file.name);
    } catch (reason) {
      notify('提交审批失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setActionId('');
    }
  }

  async function decideArtifact() {
    if (!decisionFile || actionId || (!decisionApproved && !decisionNote.trim())) return;
    setActionId(decisionFile.id);
    try {
      await api.patch(`/api/artifacts/${decisionFile.id}/decision`, { approved: decisionApproved, note: decisionNote.trim() });
      refreshArtifacts();
      notify(decisionApproved ? '成果物审批通过' : '成果物已驳回', decisionFile.name);
      setDecisionFile(null);
      setDecisionNote('');
    } catch (reason) {
      notify('成果物审批失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setActionId('');
    }
  }

  async function removeArtifact() {
    if (!deleteFile || actionId) return;
    setActionId(deleteFile.id);
    try {
      await api.delete(`/api/artifacts/${deleteFile.id}`);
      refreshArtifacts();
      notify('成果物已删除', deleteFile.name);
      setDeleteFile(null);
    } catch (reason) {
      notify('成果物删除失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setActionId('');
    }
  }

  function canSubmit(file: StageFile) {
    return !activeVersionLocked && ['draft', 'rejected'].includes(file.approvalStatus) && (canReview || file.uploadedBy === user?.id);
  }

  function openDecision(file: StageFile, approved: boolean) {
    setDecisionFile(file);
    setDecisionApproved(approved);
    setDecisionNote('');
  }

  return <div className="page">
    <PageHeader
      title="阶段里程碑与成果物"
      subtitle="按项目、年度、版本和需求层级统一归档六阶段成果"
      actions={canUpload && selectedProject ? <>
        {active?.index === 6 && <label className="compact-field">
          <span>关联当前版本需求</span>
          <select value={requirementId} onChange={(event) => setRequirementId(event.target.value)} disabled={!requirements.length}>
            {requirements.length
              ? requirements.map((item) => <option key={item.id} value={item.id}>{item.code ? `${item.code} · ` : ''}{item.title ?? `需求 #${item.id}`}</option>)
              : <option value="">当前版本暂无需求</option>}
          </select>
        </label>}
        <input ref={fileInput} type="file" className="sr-only" onChange={(event) => void upload(event.target.files)} />
        <Button disabled={uploading || submittingChange || !scopeReady} onClick={startUpload}>
          {activeVersionLocked ? <GitPullRequestArrow size={17} /> : <Upload size={17} />}
          {uploading ? '上传中...' : activeVersionLocked ? '发起附件变更' : '上传成果物'}
        </Button>
      </> : undefined}
    />
    <DataState loading={loading} error={error}>
      {selectedProject ? <>
        <div className="milestone-track" aria-label="项目六阶段里程碑">
          {stages.map((stage) => <button key={stage.id} className={`${selected === stage.id ? 'is-active ' : ''}${stage.progress === 100 ? 'is-done ' : ''}`} onClick={() => setSelected(stage.id)}>
            <i>{stage.progress === 100 ? <CheckCircle2 size={17} /> : stage.index}</i>
            <span><small>阶段 {stage.index}</small><strong>{stage.name}</strong></span>
          </button>)}
        </div>
        <div className="deliverable-layout">
          <Section className="stage-list-section" title="成果物归档">
            <div className="stage-list">{stages.map((stage) => <button key={stage.id} onClick={() => setSelected(stage.id)} className={selected === stage.id ? 'is-active' : ''}>
              <span className="stage-number">{stage.index}</span>
              <span><strong>{stage.name}</strong><small>{stage.output}</small></span>
              <div><StatusBadge tone={statusTone(stage.status)}>{stage.status}</StatusBadge><small>{stage.files.length} 个文件</small></div>
            </button>)}</div>
          </Section>
          <Section title="阶段详情">
            {active && <div className="stage-detail">
              <header>
                <span className="entity-icon"><Flag size={21} /></span>
                <div><small>阶段 {active.index}</small><h2>{active.name}</h2><p>{active.output}</p></div>
                <StatusBadge tone={statusTone(active.status)}>{active.status}</StatusBadge>
              </header>
              <dl>
                <div><dt>阶段负责角色</dt><dd>{active.owner}</dd></div>
                <div><dt>当前归档范围</dt><dd>{active.index === 1 ? '规划项目' : active.index === 2 ? '当前年度' : active.index <= 5 ? '当前版本' : '当前版本需求'}</dd></div>
                <div><dt>审批通过</dt><dd>{active.files.filter((file) => file.approvalStatus === 'approved').length} / {active.files.length}</dd></div>
              </dl>
              {activeVersionLocked && <div className="artifact-lock-notice" role="status">
                <LockKeyhole size={20} />
                <div><strong>当前版本已冻结，附件变更需审批</strong><p>新增或替换文件会先进入安全暂存区，审批并执行后才更新正式成果物和版本基线。</p></div>
              </div>}
              <div className="section__header">
                <h3>成果物文件</h3>
                {canUpload && <button className="section-link" disabled={uploading || submittingChange || !scopeReady} onClick={startUpload}>{activeVersionLocked ? <GitPullRequestArrow size={16} /> : <FilePlus2 size={16} />}{activeVersionLocked ? '申请新增附件' : '添加文件'}</button>}
              </div>
              {active.files.length ? <div className="file-list">
                {active.files.map((file) => {
                  const selfUploaded = file.uploadedBy === user?.id;
                  const showDecision = !activeVersionLocked && canReview && file.approvalStatus === 'submitted' && !selfUploaded;
                  const showDelete = canReview && !activeVersionLocked;
                  return <div key={file.id} className={file.id === targetArtifact ? 'is-target' : undefined} aria-current={file.id === targetArtifact ? 'true' : undefined}>
                    <span className="file-icon"><FileText size={19} /></span>
                    <div>
                      <strong>{file.name}</strong>
                      <small>{file.size} · 更新于 {file.updated}</small>
                      <span className="file-approval"><StatusBadge tone={statusTone(approvalLabels[file.approvalStatus])}>{approvalLabels[file.approvalStatus]}</StatusBadge>{file.reviewNote && <small>审批意见：{file.reviewNote}</small>}</span>
                    </div>
                    <div className="file-actions">
                      {canSubmit(file) && <button className="icon-button" disabled={Boolean(actionId)} title="提交审批" onClick={() => void submitArtifact(file)} aria-label={`提交 ${file.name} 审批`}><Send size={17} /></button>}
                      {showDecision && <>
                        <button className="icon-button" disabled={Boolean(actionId)} title="审批通过" onClick={() => openDecision(file, true)} aria-label={`审批通过 ${file.name}`}><CheckCircle2 size={17} /></button>
                        <button className="icon-button" disabled={Boolean(actionId)} title="驳回" onClick={() => openDecision(file, false)} aria-label={`驳回 ${file.name}`}><XCircle size={17} /></button>
                      </>}
                      {activeVersionLocked && canUpload && <button className="icon-button" disabled={submittingChange} title="申请替换附件" onClick={() => openArtifactChange(file)} aria-label={`申请替换 ${file.name}`}><FilePenLine size={17} /></button>}
                      <button className="icon-button" disabled={downloadingId === file.id} title="下载" onClick={() => void download(file)} aria-label={`下载 ${file.name}`}><Download size={18} /></button>
                      {showDelete && <button className="icon-button icon-button--danger" disabled={Boolean(actionId)} title="删除" onClick={() => setDeleteFile(file)} aria-label={`删除 ${file.name}`}><Trash2 size={17} /></button>}
                    </div>
                  </div>;
                })}
              </div> : <EmptyState icon={<Archive size={27} />} title="当前范围尚无成果物" action={canUpload ? <Button variant="secondary" disabled={uploading || submittingChange || !scopeReady} onClick={startUpload}>{activeVersionLocked ? <GitPullRequestArrow size={17} /> : <Upload size={17} />}{activeVersionLocked ? '申请新增附件' : '上传文件'}</Button> : undefined} />}
              {active.index === 5 && <div className="trigger-note"><CheckCircle2 size={20} /><div><strong>验收报告提醒</strong><p>当版本全部需求进入“待验收”“已上线运维”或“已关闭”后，系统提醒项目经理上传验收报告。</p></div></div>}
            </div>}
          </Section>
        </div>
      </> : <EmptyState icon={<Archive size={28} />} title="请先创建并选择规划项目" detail="项目建立后可按六个阶段归档成果物。" />}
    </DataState>
    {selectedVersion && <Section
      className="artifact-change-section"
      title="成果物附件变更"
      action={activeVersionLocked ? <StatusBadge tone="info">版本基线已锁定</StatusBadge> : undefined}
    >
      <DataState loading={changesLoading} error={changesError} onRetry={refreshArtifactChanges}>
        {artifactChanges.length ? <div className="change-list artifact-change-list">
          {artifactChanges.map((item) => {
            const status = item.status ?? 'pending';
            const ownRequest = String(item.requested_by ?? '') === user?.id;
            const canApproveRequest = status === 'pending' && canManageChanges && !ownRequest;
            const canCancelRequest = ['pending', 'approved'].includes(status) && (ownRequest || canManageChanges);
            const stagedFiles = item.staged_artifacts ?? [];
            const applicant = item.applicant_name || item.requester_name || (ownRequest ? '我' : `用户 #${item.requested_by ?? '-'}`);
            return <article key={item.id}>
              <span className="change-icon"><GitPullRequestArrow size={19} /></span>
              <div>
                <div><strong>{item.title || `附件变更申请 #${item.id}`}</strong><StatusBadge tone={artifactChangeTone(status)}>{artifactChangeStatusLabels[status]}</StatusBadge></div>
                <p>#{item.id} · {applicant} · {item.created_at ? new Date(item.created_at).toLocaleString('zh-CN', { hour12: false }) : '刚刚提交'}</p>
                <small>{artifactChangeSummary(item, artifacts)}</small>
                {item.reason && <small>变更原因：{item.reason}</small>}
                {item.decision_note && <small>审批意见：{item.decision_note}</small>}
                {stagedFiles.map((staged) => <div className="artifact-change-upload" key={staged.token}>
                  <FileText size={16} />
                  <span><strong>{staged.original_filename || staged.title || '暂存附件'}</strong><small>{formatFileSize(staged.size_bytes)} · 审批执行前不会覆盖正式文件</small></span>
                  {canPreviewChanges && <button
                    className="icon-button"
                    disabled={previewingToken === staged.token}
                    title="预览暂存附件"
                    aria-label={`预览暂存附件 ${staged.original_filename || staged.title || ''}`}
                    onClick={() => void previewStagedArtifact(staged)}
                  ><Eye size={17} /></button>}
                </div>)}
              </div>
              <div className="change-row-actions">
                {canCancelRequest && <Button variant="ghost" disabled={Boolean(changeActionId)} onClick={() => setCancelChange(item)}><XCircle size={16} />取消</Button>}
                {canApproveRequest && <>
                  <Button variant="danger" disabled={Boolean(changeActionId)} onClick={() => { setChangeDecision({ item, approved: false }); setChangeDecisionNote(''); }}><XCircle size={16} />驳回</Button>
                  <Button disabled={Boolean(changeActionId)} onClick={() => { setChangeDecision({ item, approved: true }); setChangeDecisionNote(''); }}><CheckCircle2 size={16} />批准</Button>
                </>}
                {status === 'approved' && canManageChanges && <Button disabled={Boolean(changeActionId)} onClick={() => void applyArtifactChange(item)}><Send size={16} />{changeActionId === String(item.id) ? '执行中...' : '执行变更'}</Button>}
              </div>
            </article>;
          })}
        </div> : <EmptyState
          icon={<GitPullRequestArrow size={28} />}
          title="当前版本暂无成果物附件变更"
          detail={activeVersionLocked ? '从上方成果物列表发起新增或替换申请，审批执行后才更新版本基线。' : '草稿版本可直接维护成果物，冻结后附件修改将在此留痕。'}
        />}
      </DataState>
    </Section>}
    <Modal
      open={changeModalOpen}
      wide
      title={changeArtifact ? '申请替换成果物附件' : '申请新增成果物附件'}
      onClose={() => !submittingChange && setChangeModalOpen(false)}
      footer={<>
        <Button variant="secondary" disabled={submittingChange} onClick={() => setChangeModalOpen(false)}>取消</Button>
        <Button
          form="artifact-change-form"
          type="submit"
          disabled={submittingChange || !changeFile || !changeForm.changeTitle.trim() || !changeForm.reason.trim() || (!changeArtifact && (!changeForm.artifactTitle.trim() || !changeForm.category.trim() || (active?.index === 6 && !changeForm.requirementId)))}
        ><GitPullRequestArrow size={17} />{submittingChange ? '提交中...' : '提交审批'}</Button>
      </>}
    >
      <form id="artifact-change-form" className="form-grid" onSubmit={(event) => void submitArtifactChange(event)}>
        <div className="artifact-change-context field--wide">
          <LockKeyhole size={20} />
          <div><strong>{options.versions.find((item) => item.id === selectedVersion)?.name || '当前版本'} · {active?.name}</strong><p>{changeArtifact ? `替换“${changeArtifact.name}”的正式附件` : '新增成果物及附件'}，审批执行前仅保存于暂存区。</p></div>
        </div>
        <label className="field field--wide"><span>申请标题</span><input maxLength={300} required value={changeForm.changeTitle} onChange={(event) => setChangeForm({ ...changeForm, changeTitle: event.target.value })} /></label>
        <label className="field field--wide"><span>变更原因</span><textarea rows={3} maxLength={10000} required value={changeForm.reason} onChange={(event) => setChangeForm({ ...changeForm, reason: event.target.value })} placeholder="说明替换或新增附件的业务原因、影响范围和核对依据" /></label>
        {!changeArtifact && <>
          <label className="field"><span>成果物标题</span><input maxLength={300} required value={changeForm.artifactTitle} onChange={(event) => setChangeForm({ ...changeForm, artifactTitle: event.target.value })} /></label>
          <label className="field"><span>成果物分类</span><input maxLength={50} required value={changeForm.category} onChange={(event) => setChangeForm({ ...changeForm, category: event.target.value })} /></label>
          {active?.index === 6 && <label className="field field--wide"><span>关联当前版本需求</span><select required value={changeForm.requirementId} onChange={(event) => setChangeForm({ ...changeForm, requirementId: event.target.value })}>
            <option value="">请选择需求</option>
            {requirements.map((item) => <option key={item.id} value={item.id}>{item.code ? `${item.code} · ` : ''}{item.title ?? `需求 #${item.id}`}</option>)}
          </select></label>}
        </>}
        <label className="field field--wide"><span>变更附件</span><input
          ref={changeFileInput}
          type="file"
          required
          onChange={(event) => {
            const file = event.target.files?.[0] ?? null;
            setChangeFile(file);
            if (file && !changeArtifact && !changeForm.artifactTitle.trim()) setChangeForm((current) => ({ ...current, artifactTitle: file.name }));
          }}
        /><small>{changeFile ? `${changeFile.name} · ${formatFileSize(changeFile.size)}` : '请选择需要暂存并送审的文件'}</small></label>
      </form>
    </Modal>
    <Modal
      open={Boolean(changeDecision)}
      title={changeDecision?.approved ? '批准附件变更申请' : '驳回附件变更申请'}
      onClose={() => setChangeDecision(null)}
      footer={<>
        <Button variant="secondary" disabled={Boolean(changeActionId)} onClick={() => setChangeDecision(null)}>取消</Button>
        <Button variant={changeDecision?.approved ? 'primary' : 'danger'} disabled={Boolean(changeActionId) || !changeDecisionNote.trim()} onClick={() => void decideArtifactChange()}>{changeActionId ? '处理中...' : changeDecision?.approved ? '确认批准' : '确认驳回'}</Button>
      </>}
    >
      <div className="form-grid">
        <p className="field field--wide">{changeDecision?.item.title}</p>
        <label className="field field--wide"><span>审批意见</span><textarea rows={4} required value={changeDecisionNote} onChange={(event) => setChangeDecisionNote(event.target.value)} placeholder="填写审批依据及执行注意事项" /></label>
      </div>
    </Modal>
    <Modal
      open={Boolean(cancelChange)}
      title="取消附件变更申请"
      onClose={() => setCancelChange(null)}
      footer={<>
        <Button variant="secondary" disabled={Boolean(changeActionId)} onClick={() => setCancelChange(null)}>返回</Button>
        <Button variant="danger" disabled={Boolean(changeActionId)} onClick={() => void cancelArtifactChange()}><XCircle size={17} />{changeActionId ? '取消中...' : '确认取消申请'}</Button>
      </>}
    >
      <p>确认取消“{cancelChange?.title}”吗？暂存附件将被清理，正式成果物不会发生变化。</p>
    </Modal>
    <Modal
      open={Boolean(decisionFile)}
      title={decisionApproved ? '审批通过成果物' : '驳回成果物'}
      onClose={() => setDecisionFile(null)}
      footer={<>
        <Button variant="secondary" disabled={Boolean(actionId)} onClick={() => setDecisionFile(null)}>取消</Button>
        <Button disabled={Boolean(actionId) || (!decisionApproved && !decisionNote.trim())} onClick={() => void decideArtifact()}>{decisionApproved ? <CheckCircle2 size={17} /> : <XCircle size={17} />}{actionId ? '处理中...' : decisionApproved ? '确认通过' : '确认驳回'}</Button>
      </>}
    >
      <div className="form-grid">
        <p className="field field--wide">{decisionFile?.name}</p>
        <label className="field field--wide"><span>{decisionApproved ? '审批意见（选填）' : '驳回原因'}</span><textarea rows={4} required={!decisionApproved} value={decisionNote} onChange={(event) => setDecisionNote(event.target.value)} /></label>
      </div>
    </Modal>
    <Modal
      open={Boolean(deleteFile)}
      title="删除成果物"
      onClose={() => setDeleteFile(null)}
      footer={<>
        <Button variant="secondary" disabled={Boolean(actionId)} onClick={() => setDeleteFile(null)}>取消</Button>
        <Button disabled={Boolean(actionId)} onClick={() => void removeArtifact()}><Trash2 size={17} />{actionId ? '删除中...' : '确认删除'}</Button>
      </>}
    >
      <p>确认删除“{deleteFile?.name}”及其服务器附件吗？此操作不可撤销。</p>
    </Modal>
  </div>;
}

interface Ticket {
  id: string;
  title: string;
  type: string;
  status: string;
  statusCode: string;
  version: string;
  reporter: string;
  createdAt: string;
  originalRequirement?: string;
  description: string;
}

interface OperationApi {
  id: number;
  title: string;
  content: string;
  feedback_type: string;
  status: string;
  version_id?: number | null;
  requirement_id?: number | null;
  reporter_id?: number;
  created_at: string;
}

const ticketTypeLabels: Record<string, string> = {
  issue: '问题反馈',
  bug: '线上 Bug',
  promotion: '推广维护',
  question: '问题解答',
  improvement: '功能建议',
};
const ticketTypeCodes: Record<string, string> = Object.fromEntries(Object.entries(ticketTypeLabels).map(([code, label]) => [label, code]));
const ticketStatusLabels: Record<string, string> = { open: '待评估', processing: '处理中', resolved: '已解决', closed: '已关闭' };
const ticketStatusCodes: Record<string, string> = Object.fromEntries(Object.entries(ticketStatusLabels).map(([code, label]) => [label, code]));

function ticketFromApi(raw: OperationApi, versionName: (id: string) => string): Ticket {
  return {
    id: String(raw.id),
    title: raw.title,
    type: ticketTypeLabels[raw.feedback_type] ?? raw.feedback_type,
    status: ticketStatusLabels[raw.status] ?? raw.status,
    statusCode: raw.status,
    version: raw.version_id ? versionName(String(raw.version_id)) : '未关联版本',
    reporter: raw.reporter_id ? `用户 #${raw.reporter_id}` : '-',
    createdAt: raw.created_at ? new Date(raw.created_at).toLocaleString('zh-CN', { hour12: false }) : '',
    originalRequirement: raw.requirement_id ? String(raw.requirement_id) : undefined,
    description: raw.content ?? '',
  };
}

function ticketItems(payload: OperationApi[] | { items: OperationApi[] } | { data: OperationApi[] }, versionName: (id: string) => string) {
  const values = Array.isArray(payload) ? payload : 'items' in payload ? unwrapItems(payload) : payload.data;
  return values.map((value) => ticketFromApi(value, versionName));
}

export function OperationsPage() {
  const [searchParams] = useSearchParams();
  const targetOperation = searchParams.get('operation_id') ?? '';
  const { notify, selectedProject, selectedVersion, options, user } = useApp();
  const operationPath = selectedProject ? `/api/operations?project_id=${encodeURIComponent(selectedProject)}` : '';
  const { data: raw, setData, loading, error } = useApiData<OperationApi[] | { items: OperationApi[] } | { data: OperationApi[] }>(operationPath, [], [selectedProject]);
  const versionName = (id: string) => options.versions.find((item) => item.id === id)?.name ?? `版本 #${id}`;
  const items = ticketItems(raw, versionName);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('');
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<Ticket | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [updatingId, setUpdatingId] = useState('');
  const [form, setForm] = useState({ title: '', type: '线上 Bug', version: selectedVersion, originalRequirement: '', description: '' });
  const projectYears = useMemo(() => new Set(options.years.filter((item) => item.parentId === selectedProject).map((item) => item.id)), [options.years, selectedProject]);
  const availableVersions = useMemo(() => options.versions.filter((item) => projectYears.has(item.parentId ?? '')), [options.versions, projectYears]);
  const requirementPath = selectedProject && form.version
    ? `/api/requirements?project_id=${encodeURIComponent(selectedProject)}&version_id=${encodeURIComponent(form.version)}`
    : '';
  const { data: requirementRaw, loading: requirementsLoading, error: requirementsError } = useApiData<RequirementOption[] | { items: RequirementOption[] }>(requirementPath, [], [selectedProject, form.version]);
  const requirementOptions = Array.isArray(requirementRaw) ? requirementRaw : unwrapItems(requirementRaw);
  const filtered = useMemo(
    () => items.filter((item) => (!query || `${item.id}${item.title}`.toLowerCase().includes(query.toLowerCase())) && (!status || item.status === status)),
    [items, query, status],
  );
  const canManage = ['admin', 'operator', 'manager', 'leader'].includes(user?.role ?? '');
  useEffect(() => {
    if (!targetOperation || detail?.id === targetOperation) return;
    const target = items.find((item) => item.id === targetOperation);
    if (target) setDetail(target);
  }, [targetOperation, items, detail?.id]);

  function openCreate() {
    const version = availableVersions.some((item) => item.id === selectedVersion) ? selectedVersion : availableVersions[0]?.id ?? '';
    setForm({ title: '', type: '线上 Bug', version, originalRequirement: '', description: '' });
    setOpen(true);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (submitting || !selectedProject) return;
    setSubmitting(true);
    try {
      const created = await api.post<OperationApi>('/api/operations', {
        project_id: Number(selectedProject),
        version_id: form.version ? Number(form.version) : null,
        requirement_id: form.originalRequirement ? Number(form.originalRequirement) : null,
        title: form.title,
        content: form.description,
        feedback_type: ticketTypeCodes[form.type] ?? 'issue',
      });
      setData([created, ...(Array.isArray(raw) ? raw : 'items' in raw ? unwrapItems(raw) : raw.data)]);
      setOpen(false);
      notify('运营工单已提交');
    } catch (reason) {
      notify('运营工单提交失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setSubmitting(false);
    }
  }

  async function update(ticket: Ticket, nextStatus: string) {
    if (updatingId || !ticketStatusLabels[nextStatus]) return;
    setUpdatingId(ticket.id);
    try {
      const updated = await api.patch<OperationApi>(`/api/operations/${ticket.id}`, { status: nextStatus });
      const source = Array.isArray(raw) ? raw : 'items' in raw ? unwrapItems(raw) : raw.data;
      setData(source.map((item) => String(item.id) === ticket.id ? updated : item));
      const merged = ticketFromApi(updated, versionName);
      setDetail(merged);
      notify('工单状态已更新', `${ticket.title}：${merged.status}`);
    } catch (reason) {
      notify('工单更新失败', reason instanceof Error ? reason.message : '请稍后重试。', 'danger');
    } finally {
      setUpdatingId('');
    }
  }

  function ticketIcon(type: string) {
    if (type === '线上 Bug') return <ServerCog size={18} />;
    if (type === '推广维护') return <Megaphone size={18} />;
    return <Rocket size={18} />;
  }

  return <div className="page">
    <PageHeader title="运营服务" subtitle="线上问题、问题解答、功能建议与推广维护的统一闭环" actions={selectedProject ? <Button onClick={openCreate}><Plus size={17} />提交工单</Button> : undefined} />
    <DataState loading={loading} error={error}>
      <div className="metrics-grid">
        <Metric label="全部工单" value={items.length} icon={<MessageSquareText size={19} />} />
        <Metric label="待评估" value={items.filter((item) => item.statusCode === 'open').length} tone="warning" />
        <Metric label="处理中" value={items.filter((item) => item.statusCode === 'processing').length} tone="warning" icon={<Wrench size={19} />} />
        <Metric label="已完成" value={items.filter((item) => ['resolved', 'closed'].includes(item.statusCode)).length} tone="success" icon={<CheckCircle2 size={19} />} />
      </div>
      <div className="filter-bar">
        <label className="search-input"><Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工单 ID 或标题" aria-label="搜索工单" /></label>
        <label className="select-filter"><select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="工单状态"><option value="">全部状态</option>{Object.values(ticketStatusLabels).map((value) => <option key={value}>{value}</option>)}</select></label>
      </div>
      <Section>
        {filtered.length ? <div className="ticket-list">{filtered.map((ticket) => <button key={ticket.id} onClick={() => setDetail(ticket)}>
          <span className={`ticket-type ticket-type--${ticket.type === '线上 Bug' ? 'bug' : ticket.type === '推广维护' ? 'campaign' : 'feature'}`}>{ticketIcon(ticket.type)}</span>
          <div><div><strong>{ticket.title}</strong><small>{ticket.type}</small></div><p>工单 #{ticket.id} · {ticket.version} · {ticket.reporter}</p></div>
          <StatusBadge tone={statusTone(ticket.status)}>{ticket.status}</StatusBadge>
          <ExternalLink size={17} />
        </button>)}</div> : <EmptyState icon={<MessageSquareText size={28} />} title="当前筛选条件下暂无运营工单" />}
      </Section>
    </DataState>
    <Modal open={open} title="提交运营工单" wide onClose={() => setOpen(false)} footer={<><Button variant="secondary" disabled={submitting} onClick={() => setOpen(false)}>取消</Button><Button type="submit" form="ticket-form" disabled={submitting}>{submitting ? '提交中...' : '提交工单'}</Button></>}>
      <form id="ticket-form" className="form-grid" onSubmit={(event) => void submit(event)}>
        <label className="field field--wide"><span>工单标题</span><input value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} required /></label>
        <label className="field"><span>工单类型</span><select value={form.type} onChange={(event) => setForm({ ...form, type: event.target.value })}>{Object.values(ticketTypeLabels).map((value) => <option key={value}>{value}</option>)}</select></label>
        <label className="field"><span>影响版本（选填）</span><select value={form.version} onChange={(event) => setForm({ ...form, version: event.target.value, originalRequirement: '' })}><option value="">不关联版本</option>{availableVersions.map((version) => <option value={version.id} key={version.id}>{version.name}</option>)}</select></label>
        <label className="field field--wide"><span>关联原需求（选填）</span><select value={form.originalRequirement} onChange={(event) => setForm({ ...form, originalRequirement: event.target.value })} disabled={!form.version || requirementsLoading || Boolean(requirementsError)}><option value="">{requirementsLoading ? '需求加载中...' : requirementsError ? '需求加载失败' : form.version ? '不关联原需求' : '请先选择影响版本'}</option>{requirementOptions.map((item) => <option key={item.id} value={item.id}>{item.code ? `${item.code} · ` : ''}{item.title ?? `需求 #${item.id}`}</option>)}</select>{requirementsError && <small>{requirementsError}</small>}</label>
        <label className="field field--wide"><span>问题或服务说明</span><textarea rows={5} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} required /></label>
      </form>
    </Modal>
    <Modal
      open={Boolean(detail)}
      title={detail ? `工单 #${detail.id}` : '工单详情'}
      wide
      onClose={() => setDetail(null)}
      footer={detail && canManage ? <>
        {detail.statusCode === 'open' && <Button disabled={updatingId === detail.id} onClick={() => void update(detail, 'processing')}><Wrench size={17} />开始处理</Button>}
        {detail.statusCode === 'processing' && <Button disabled={updatingId === detail.id} onClick={() => void update(detail, 'resolved')}><CheckCircle2 size={17} />标记已解决</Button>}
        {detail.statusCode === 'resolved' && <><Button variant="secondary" disabled={updatingId === detail.id} onClick={() => void update(detail, 'processing')}><Wrench size={17} />重新处理</Button><Button disabled={updatingId === detail.id} onClick={() => void update(detail, 'closed')}><CheckCircle2 size={17} />关闭工单</Button></>}
      </> : undefined}
    >
      <div className="ticket-detail">{detail && <>
        <div className="detail-heading"><div><small>{detail.type} · {detail.version}</small><h3>{detail.title}</h3></div><StatusBadge tone={statusTone(detail.status)}>{detail.status}</StatusBadge></div>
        <p>{detail.description}</p>
        <dl><div><dt>提交人</dt><dd>{detail.reporter}</dd></div><div><dt>提交时间</dt><dd>{detail.createdAt}</dd></div><div><dt>关联原需求</dt><dd>{detail.originalRequirement ? `需求 #${detail.originalRequirement}` : '无'}</dd></div><div><dt>当前状态</dt><dd>{detail.status}</dd></div></dl>
        <div className="activity-list"><div><i><CircleDot size={15} /></i><span><strong>工单已创建</strong><small>{detail.createdAt}</small></span></div>{detail.statusCode !== 'open' && <div><i><Wrench size={15} /></i><span><strong>已进入处理流程</strong><small>当前状态：{detail.status}</small></span></div>}{['resolved', 'closed'].includes(detail.statusCode) && <div><i><CheckCircle2 size={15} /></i><span><strong>{detail.statusCode === 'closed' ? '工单已关闭' : '问题已解决'}</strong><small>处理状态已记录</small></span></div>}</div>
      </>}</div>
    </Modal>
  </div>;
}
