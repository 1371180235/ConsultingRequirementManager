// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import App from '../App';
import { AppProvider } from '../state/AppContext';
import { isArtifactStageLocked } from './DeliveryPages';

interface FetchOptions {
  changes?: Array<Record<string, unknown>>;
  onRequest?: (path: string, init?: RequestInit) => Response | undefined;
}

function json(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  }));
}

function stubWorkspaceFetch({ changes = [], onRequest }: FetchOptions = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    const custom = onRequest?.(path, init);
    if (custom) return Promise.resolve(custom);
    if (path.includes('/api/auth/me')) return json({ id: 1, username: 'admin', full_name: '测试管理员', role: 'admin', csrf_token: 'test-csrf' });
    if (path.includes('/api/context')) return json({
      projects: [{ id: 1, code: 'P-001', name: '测试项目' }],
      annual_plans: [{ id: 10, project_id: 1, year: 2026, name: '2026 年度计划' }],
      versions: [{ id: 100, annual_plan_id: 10, code: 'V1.0', name: '冻结版本', status: 'frozen' }],
      tags: [],
    });
    if (path.startsWith('/api/change-requests?')) return json(changes);
    if (path.startsWith('/api/requirements?')) return json([{ id: 501, code: 'REQ-501', title: '上线保障' }]);
    if (path.startsWith('/api/artifacts?')) {
      const query = new URL(path, 'http://localhost').searchParams;
      if (query.get('version_id') === '100') return json([{
        id: 33,
        project_id: 1,
        annual_plan_id: 10,
        version_id: 100,
        stage: 3,
        category: '任务书方案、需求清单',
        title: '一期任务书',
        original_filename: '任务书-v1.pdf',
        size_bytes: 2048,
        has_file: true,
        approval_status: 'approved',
        uploaded_by: 2,
        created_at: '2026-07-16T08:00:00Z',
      }]);
      return json([]);
    }
    return json([]);
  });
}

async function renderFrozenDeliverables() {
  render(<MemoryRouter initialEntries={['/deliverables']}><AppProvider><App /></AppProvider></MemoryRouter>);
  expect(await screen.findByRole('heading', { level: 1, name: '阶段里程碑与成果物' })).toBeInTheDocument();
  const stageLabels = await screen.findAllByText('建设落地');
  fireEvent.click(stageLabels[0].closest('button') as HTMLButtonElement);
  expect(await screen.findByText('当前版本已冻结，附件变更需审批')).toBeInTheDocument();
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
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('frozen version artifact changes', () => {
  it('locks version-scoped stages 3 through 6 only after the version leaves draft', () => {
    expect(isArtifactStageLocked(2, 'frozen')).toBe(false);
    expect(isArtifactStageLocked(3, 'draft')).toBe(false);
    expect(isArtifactStageLocked(3, 'frozen')).toBe(true);
    expect(isArtifactStageLocked(6, 'released')).toBe(true);
  });

  it('submits both add and replacement files through the multipart change endpoint', async () => {
    const uploads: FormData[] = [];
    let nextId = 80;
    vi.stubGlobal('fetch', stubWorkspaceFetch({
      onRequest: (path, init) => {
        if (!path.includes('/artifact-change-requests/upload') || init?.method !== 'POST') return undefined;
        const form = init.body as FormData;
        uploads.push(form);
        const id = nextId++;
        const artifactId = form.get('artifact_id');
        const operation = artifactId
          ? { action: 'replace_file', artifact_id: Number(artifactId), upload_token: `${id}`.padStart(32, '0') }
          : { action: 'add', data: { stage: Number(form.get('stage')), category: form.get('category'), title: form.get('artifact_title'), upload_token: `${id}`.padStart(32, '0') } };
        return new Response(JSON.stringify({
          change_request: {
            id,
            version_id: 100,
            status: 'pending',
            title: form.get('change_title'),
            reason: form.get('reason'),
            change_type: 'artifact_file',
            requested_by: 1,
            payload: { artifacts: [operation] },
          },
          staged_artifact: {
            token: `${id}`.padStart(32, '0'),
            version_id: 100,
            change_request_id: id,
            stage: 3,
            title: form.get('artifact_title') || '一期任务书',
            original_filename: (form.get('file') as File).name,
            size_bytes: (form.get('file') as File).size,
          },
        }), { status: 201, headers: { 'Content-Type': 'application/json' } });
      },
    }));

    await renderFrozenDeliverables();
    expect(screen.queryByRole('button', { name: /提交 .*审批/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '发起附件变更' }));
    expect(await screen.findByRole('heading', { name: '申请新增成果物附件' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('申请标题'), { target: { value: '新增交付清单附件' } });
    fireEvent.change(screen.getByLabelText('变更原因'), { target: { value: '补齐冻结基线遗漏的签章文件' } });
    fireEvent.change(screen.getByLabelText(/^变更附件/), { target: { files: [new File(['new'], '交付清单.pdf', { type: 'application/pdf' })] } });
    await waitFor(() => expect(screen.getByRole('button', { name: '提交审批' })).toBeEnabled());
    fireEvent.submit(document.getElementById('artifact-change-form') as HTMLFormElement);

    await waitFor(() => expect(uploads).toHaveLength(1));
    expect(uploads[0].get('artifact_title')).toBe('交付清单.pdf');
    expect(uploads[0].get('stage')).toBe('3');
    expect(uploads[0].get('category')).toBe('任务书方案、需求清单');
    expect(uploads[0].get('artifact_id')).toBeNull();
    const ownRequest = screen.getByText('新增交付清单附件').closest('article') as HTMLElement;
    expect(within(ownRequest).queryByRole('button', { name: '批准' })).not.toBeInTheDocument();
    expect(within(ownRequest).getByRole('button', { name: '取消' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '申请替换 任务书-v1.pdf' }));
    expect(await screen.findByRole('heading', { name: '申请替换成果物附件' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('变更原因'), { target: { value: '替换为客户最终签章版本' } });
    fireEvent.change(screen.getByLabelText(/^变更附件/), { target: { files: [new File(['signed'], '任务书-签章版.pdf', { type: 'application/pdf' })] } });
    await waitFor(() => expect(screen.getByRole('button', { name: '提交审批' })).toBeEnabled());
    fireEvent.submit(document.getElementById('artifact-change-form') as HTMLFormElement);

    await waitFor(() => expect(uploads).toHaveLength(2));
    expect(uploads[1].get('artifact_id')).toBe('33');
    expect(uploads[1].get('artifact_title')).toBeNull();
    expect(uploads[1].get('stage')).toBeNull();
    expect(screen.getAllByText('文件已安全暂存，审批并执行后才会写入冻结版本。')).toHaveLength(2);
  });

  it('exposes approval, execution and cancellation actions according to request state', async () => {
    const requests: Array<{ path: string; init?: RequestInit }> = [];
    const changes = [
      {
        id: 70,
        version_id: 100,
        title: '待审批附件替换',
        reason: '签章文件更新',
        change_type: 'artifact_file',
        status: 'pending',
        requested_by: 2,
        created_at: '2026-07-16T09:00:00Z',
        payload: { artifacts: [{ action: 'replace_file', artifact_id: 33, upload_token: 'a'.repeat(32) }] },
        staged_artifacts: [{ token: 'a'.repeat(32), original_filename: '任务书-签章版.pdf', size_bytes: 4096, stage: 3 }],
      },
      {
        id: 71,
        version_id: 100,
        title: '已批准新增附件',
        reason: '补齐附件',
        change_type: 'artifact_file',
        status: 'approved',
        requested_by: 1,
        payload: { artifacts: [{ action: 'add', data: { stage: 3, title: '交付清单', upload_token: 'b'.repeat(32) } }] },
        staged_artifacts: [{ token: 'b'.repeat(32), original_filename: '交付清单.pdf', size_bytes: 5120, stage: 3 }],
      },
    ];
    vi.stubGlobal('fetch', stubWorkspaceFetch({
      changes,
      onRequest: (path, init) => {
        if (!path.startsWith('/api/change-requests/')) return undefined;
        requests.push({ path, init });
        return new Response(JSON.stringify({ id: Number(path.split('/')[3]), status: path.endsWith('/apply') ? 'applied' : path.endsWith('/cancel') ? 'cancelled' : 'approved' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      },
    }));

    await renderFrozenDeliverables();
    const pending = (await screen.findByText('待审批附件替换')).closest('article') as HTMLElement;
    expect(within(pending).getByRole('button', { name: '预览暂存附件 任务书-签章版.pdf' })).toBeInTheDocument();
    fireEvent.click(within(pending).getByRole('button', { name: '批准' }));
    fireEvent.change(screen.getByLabelText('审批意见'), { target: { value: '附件与签章记录一致' } });
    fireEvent.click(screen.getByRole('button', { name: '确认批准' }));
    await waitFor(() => expect(requests.some((item) => item.path === '/api/change-requests/70' && item.init?.method === 'PATCH')).toBe(true));

    const approved = (await screen.findByText('已批准新增附件')).closest('article') as HTMLElement;
    fireEvent.click(within(approved).getByRole('button', { name: '执行变更' }));
    await waitFor(() => expect(requests.some((item) => item.path === '/api/change-requests/71/apply' && item.init?.method === 'POST')).toBe(true));

    fireEvent.click(within(approved).getByRole('button', { name: '取消' }));
    fireEvent.click(screen.getByRole('button', { name: '确认取消申请' }));
    await waitFor(() => expect(requests.some((item) => item.path === '/api/change-requests/71/cancel' && item.init?.method === 'POST')).toBe(true));
  });
});
