import type {
  FileEntry,
  Message,
  Project,
  ServerInfo,
  Session,
  ThemeName
} from './types';

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (init.body && !(init.body instanceof Blob) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || JSON.stringify(payload);
    } catch {
      /* 保留状态文本 */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const client = {
  authStatus() {
    return api<{ authenticated: boolean; setup_required: boolean }>('/api/auth/status');
  },
  setup(password: string) {
    return api<{ ok: boolean }>('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ password })
    });
  },
  login(password: string) {
    return api<{ ok: boolean }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password })
    });
  },
  changePassword(current_password: string, new_password: string) {
    return api<{ ok: boolean }>('/api/auth/password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password })
    });
  },
  overview() {
    return api<{ projects: Project[] }>('/api/overview');
  },
  createProject(name: string) {
    return api<{ project: Project }>('/api/projects', {
      method: 'POST',
      body: JSON.stringify({ name })
    });
  },
  updateProject(id: string, patch: { name?: string; pinned?: boolean }) {
    return api<{ project: Project }>(`/api/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch)
    });
  },
  deleteProject(id: string) {
    return api<{ ok: boolean }>(`/api/projects/${id}`, { method: 'DELETE' });
  },
  createSession(projectId: string) {
    return api<{ session: Session }>(`/api/projects/${projectId}/sessions`, {
      method: 'POST',
      body: JSON.stringify({})
    });
  },
  updateSession(id: string, patch: { title?: string; pinned?: boolean }) {
    return api<{ session: Session }>(`/api/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch)
    });
  },
  deleteSession(id: string) {
    return api<{ ok: boolean }>(`/api/sessions/${id}`, { method: 'DELETE' });
  },
  messages(sessionId: string, afterSeq?: number) {
    const query = afterSeq ? `?after_seq=${afterSeq}` : '';
    return api<{ session: Session; messages: Message[]; has_more: boolean }>(
      `/api/sessions/${sessionId}/messages${query}`
    );
  },
  send(sessionId: string, prompt: string) {
    return api<{ job_id: string; message: Message; status: string }>(
      `/api/sessions/${sessionId}/turns`,
      { method: 'POST', body: JSON.stringify({ prompt }) }
    );
  },
  interrupt(sessionId: string) {
    return api<{ status: string }>(`/api/sessions/${sessionId}/interrupt`, {
      method: 'POST'
    });
  },
  sessionStatus(sessionId: string) {
    return api<{ running_job_id: string | null; queued_jobs: number }>(
      `/api/sessions/${sessionId}/status`
    );
  },
  files(projectId: string, path = '') {
    return api<{ path: string; entries: FileEntry[] }>(
      `/api/projects/${projectId}/files?path=${encodeURIComponent(path)}`
    );
  },
  deleteEntry(projectId: string, path: string) {
    return api<{ ok: boolean; kind: string }>(
      `/api/projects/${projectId}/files?path=${encodeURIComponent(path)}`,
      { method: 'DELETE' }
    );
  },
  downloadUrl(projectId: string, path: string) {
    return `/api/projects/${projectId}/files/download?path=${encodeURIComponent(path)}`;
  },
  serverInfo() {
    return api<ServerInfo>('/api/server-info');
  }
};

interface UploadHandle {
  upload_id: string;
  chunk_size: number;
  total_parts: number;
  received_parts: number;
  relative_path: string;
}

const resumeKey = (projectId: string, file: File) =>
  `ral-upload:${projectId}:${file.name}:${file.size}`;

export async function uploadFile(
  projectId: string,
  file: File,
  onProgress: (sent: number, total: number) => void
): Promise<void> {
  let uploadId = localStorage.getItem(resumeKey(projectId, file)) || '';
  let chunkSize = 5 * 1024 * 1024;
  let totalParts = Math.max(1, Math.ceil(file.size / chunkSize));
  let received = 0;

  if (uploadId) {
    try {
      const resumed = await api<UploadHandle>(`/api/uploads/${uploadId}`);
      chunkSize = resumed.chunk_size;
      totalParts = resumed.total_parts;
      received = resumed.received_parts;
    } catch {
      localStorage.removeItem(resumeKey(projectId, file));
      uploadId = '';
    }
  }

  if (!uploadId) {
    const init = await api<UploadHandle & { upload_id: string }>(
      `/api/projects/${projectId}/uploads/init`,
      { method: 'POST', body: JSON.stringify({ filename: file.name, size: file.size }) }
    );
    uploadId = init.upload_id;
    chunkSize = init.chunk_size;
    totalParts = init.total_parts;
    received = init.received_parts;
    localStorage.setItem(resumeKey(projectId, file), uploadId);
  }

  onProgress(Math.min(received * chunkSize, file.size), file.size);
  for (let index = received; index < totalParts; index += 1) {
    const start = index * chunkSize;
    const end = Math.min(file.size, start + chunkSize);
    const chunk = file.slice(start, end);
    let attempt = 0;
    for (;;) {
      try {
        await api(`/api/uploads/${uploadId}/parts/${index}`, {
          method: 'PUT',
          body: chunk,
          headers: { 'Content-Type': 'application/octet-stream' }
        });
        break;
      } catch (error) {
        attempt += 1;
        if (attempt >= 3) throw error;
        await new Promise((resolve) => setTimeout(resolve, 500 * attempt));
      }
    }
    onProgress(end, file.size);
  }
  await api(`/api/uploads/${uploadId}/complete`, {
    method: 'POST',
    body: JSON.stringify({})
  });
  localStorage.removeItem(resumeKey(projectId, file));
}

export type EventHandler = (payload: Record<string, unknown>) => void;

export function subscribeEvents(
  sessionId: string | null,
  handlers: Record<string, EventHandler>
): () => void {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : '';
  const source = new EventSource(`/api/events${query}`);
  for (const [type, handler] of Object.entries(handlers)) {
    source.addEventListener(type, (event) => {
      try {
        handler(JSON.parse((event as MessageEvent).data));
      } catch {
        handler({});
      }
    });
  }
  return () => source.close();
}

export type { ThemeName };
