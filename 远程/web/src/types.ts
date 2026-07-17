export type Role = 'admin' | 'leader' | 'customer' | 'sales' | 'manager' | 'developer' | 'operator';

export interface User {
  id: string;
  name: string;
  username: string;
  role: Role;
  roleLabel: string;
  firstLogin?: boolean;
  projects?: string[];
}

export interface ContextOption {
  id: string;
  name: string;
  parentId?: string;
  status?: string;
}

export interface WorkspaceOptions {
  projects: ContextOption[];
  years: ContextOption[];
  versions: ContextOption[];
  tags?: ContextOption[];
}

export interface Requirement {
  id: string;
  resourceId?: string;
  title: string;
  description?: string;
  project: string;
  version: string;
  source: string;
  owner: string;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  status: string;
  budget: number;
  tags: string[];
  updatedAt: string;
  sourceRequirementId?: string;
  actualHours?: number;
  requesterId?: string;
  stableKey?: string;
}

export interface ToastMessage {
  id: number;
  title: string;
  detail?: string;
  tone?: 'success' | 'warning' | 'danger' | 'info';
}
