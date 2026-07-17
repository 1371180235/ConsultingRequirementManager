import type { Requirement, WorkspaceOptions } from './types';

export const workspaceOptions: WorkspaceOptions = {
  projects: [
    { id: 'p1', name: '城市数字化能力提升项目', status: '建设中' },
    { id: 'p2', name: '企业服务一体化平台', status: '规划中' },
    { id: 'p3', name: '数据治理与运营项目', status: '运维中' },
  ],
  years: [
    { id: 'y2026', name: '2026 年度计划', parentId: 'p1' },
    { id: 'y2025', name: '2025 年度计划', parentId: 'p1' },
    { id: 'y2-2026', name: '2026 年度计划', parentId: 'p2' },
    { id: 'y3-2026', name: '2026 年度计划', parentId: 'p3' },
  ],
  versions: [
    { id: 'v1.2', name: 'V1.2 治理能力版', parentId: 'y2026', status: '研发中' },
    { id: 'v1.1', name: 'V1.1 业务协同版', parentId: 'y2026', status: '已发布' },
    { id: 'v1.0', name: 'V1.0 基础版', parentId: 'y2025', status: '已归档' },
    { id: 'v2.0', name: 'V2.0 年度建设版', parentId: 'y2-2026', status: '规划中' },
  ],
};

export const requirements: Requirement[] = [
  { id: 'REQ-2026-031', title: '多源需求统一收口与去重', project: '城市数字化能力提升项目', version: 'V1.2', source: '客户', owner: '张慧', priority: 'P0', status: '研发中', budget: 42, tags: ['业务痛点', '核心能力'], updatedAt: '2026-07-16 10:24' },
  { id: 'REQ-2026-028', title: '资金链路四级穿透查询', project: '城市数字化能力提升项目', version: 'V1.2', source: '内部销售', owner: '李明', priority: 'P1', status: '待验收', budget: 36, tags: ['资金管理'], updatedAt: '2026-07-16 09:41' },
  { id: 'REQ-2026-024', title: '客户视角项目进展看板', project: '城市数字化能力提升项目', version: 'V1.1', source: '项目经理', owner: '王珊', priority: 'P1', status: '已上线运维', budget: 25, tags: ['功能优化'], updatedAt: '2026-07-15 16:18' },
  { id: 'REQ-2026-019', title: '线上问题反馈关联原需求', project: '城市数字化能力提升项目', version: '待规划', source: '运营服务', owner: '陈宇', priority: 'P2', status: '规划中', budget: 8, tags: ['运维Bug'], updatedAt: '2026-07-15 14:02' },
  { id: 'REQ-2026-017', title: '版本基线冻结与变更审批', project: '城市数字化能力提升项目', version: 'V1.2', source: '研发交付', owner: '赵磊', priority: 'P1', status: '已排期', budget: 31, tags: ['招投标要求'], updatedAt: '2026-07-14 18:36' },
  { id: 'REQ-2026-012', title: '年度申报书成果物归档', project: '城市数字化能力提升项目', version: 'V1.1', source: '项目负责人', owner: '刘洋', priority: 'P2', status: '已上线运维', budget: 18, tags: ['成果物'], updatedAt: '2026-07-13 11:20' },
];

export const monthlyTrend = [
  { month: '2月', added: 12, done: 7 },
  { month: '3月', added: 18, done: 13 },
  { month: '4月', added: 15, done: 16 },
  { month: '5月', added: 23, done: 18 },
  { month: '6月', added: 19, done: 21 },
  { month: '7月', added: 16, done: 14 },
];

export const budgetDistribution = [
  { name: '业务协同', value: 228, color: '#2563eb' },
  { name: '数据治理', value: 176, color: '#059669' },
  { name: '平台建设', value: 132, color: '#d97706' },
  { name: '运营服务', value: 84, color: '#64748b' },
];

export const roleLabels: Record<string, string> = {
  admin: '管理员',
  leader: '咨询负责人',
  customer: '客户',
  sales: '销售',
  manager: '项目经理',
  developer: '研发人员',
  operator: '运营人员',
};
