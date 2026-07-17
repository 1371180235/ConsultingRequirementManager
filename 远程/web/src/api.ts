export class ApiError extends Error {
  status: number;
  payload?: unknown;

  constructor(message: string, status: number, payload?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

function readCookie(name: string): string | undefined {
  return document.cookie
    .split('; ')
    .find((entry) => entry.startsWith(`${name}=`))
    ?.split('=')
    .slice(1)
    .join('=');
}

let csrfToken: string | undefined;

function getCsrfToken(): string | undefined {
  return csrfToken ?? readCookie('csrf_token') ?? readCookie('csrftoken');
}

export function setCsrfToken(token?: string): void {
  csrfToken = token;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const isForm = init.body instanceof FormData;
  if (init.body && !isForm && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const csrf = getCsrfToken();
  if (csrf && !['GET', 'HEAD', 'OPTIONS'].includes((init.method ?? 'GET').toUpperCase())) {
    headers.set('X-CSRF-Token', decodeURIComponent(csrf));
  }

  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers,
      credentials: 'include',
    });
  } catch {
    throw new ApiError('无法连接服务器，请检查网络或服务运行状态。', 0);
  }

  const contentType = response.headers.get('content-type') ?? '';
  const payload = contentType.includes('application/json')
    ? await response.json().catch(() => undefined)
    : await response.text().catch(() => undefined);

  if (!response.ok) {
    const detail = typeof payload === 'object' && payload && 'detail' in payload
      ? (payload as { detail: unknown }).detail
      : undefined;
    const detailMessage = typeof detail === 'string'
      ? detail
      : typeof detail === 'object' && detail && 'message' in detail
        ? String((detail as { message: unknown }).message)
        : undefined;
    const detailCode = typeof detail === 'object' && detail && 'code' in detail
      ? String((detail as { code: unknown }).code)
      : undefined;
    const message = detailMessage
        ? detailCode ? `${detailMessage}（${detailCode}）` : detailMessage
        : typeof payload === 'object' && payload && 'message' in payload
          ? String((payload as { message: unknown }).message)
          : response.status === 401
            ? '登录已失效，请重新登录。'
            : `请求失败（${response.status}）`;

    if (response.status === 401 && !path.endsWith('/auth/login') && !path.endsWith('/auth/me')) {
      window.dispatchEvent(new CustomEvent('crm:session-expired', { detail: message }));
    }
    throw new ApiError(message, response.status, payload);
  }

  if (response.status === 204) return undefined as T;
  return payload as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body instanceof FormData ? body : JSON.stringify(body ?? {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body instanceof FormData ? body : JSON.stringify(body ?? {}) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body ?? {}) }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};

export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(path, { credentials: 'include' });
  } catch {
    throw new ApiError('无法连接服务器，请检查网络或服务运行状态。', 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string | { message?: string } } | null;
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message ?? `下载请求失败（${response.status}）`;
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent('crm:session-expired', { detail: message }));
    }
    throw new ApiError(message, response.status, payload);
  }
  const disposition = response.headers.get('content-disposition') ?? '';
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  const name = encodedName ? decodeURIComponent(encodedName) : plainName ?? fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function unwrapItems<T>(payload: T[] | { items: T[] }): T[] {
  return Array.isArray(payload) ? payload : payload.items;
}

export function apiId(value: string | number | undefined | null): number | null {
  if (value === undefined || value === null || value === '') return null;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) throw new Error('当前上下文标识无效，请重新选择项目、年度或版本。');
  return parsed;
}
