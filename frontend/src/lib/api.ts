import type { FileEntry, GitCommit, Message, Project, ServerInfo, Session } from './types';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || JSON.stringify(payload);
    } catch {
      // Keep status text.
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const client = {
  async status() {
    return api<{ authenticated: boolean; setup_required: boolean }>('/api/auth/status');
  },
  async setup(password: string) {
    return api<{ ok: boolean }>('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({ password })
    });
  },
  async login(password: string) {
    return api<{ ok: boolean }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ password })
    });
  },
  async logout() {
    return api<{ ok: boolean }>('/api/auth/logout', { method: 'POST' });
  },
  async changePassword(current_password: string, new_password: string) {
    return api<{ ok: boolean }>('/api/auth/password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password })
    });
  },
  async projects() {
    return (await api<{ projects: Project[] }>('/api/projects')).projects;
  },
  async createProject(name: string) {
    return (
      await api<{ project: Project }>('/api/projects', {
        method: 'POST',
        body: JSON.stringify({ name })
      })
    ).project;
  },
  async renameProject(id: string, name: string) {
    return (
      await api<{ project: Project }>(`/api/projects/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name })
      })
    ).project;
  },
  async trashProject(id: string) {
    return api(`/api/projects/${id}`, { method: 'DELETE' });
  },
  async restoreProject(id: string) {
    return api(`/api/projects/${id}/restore`, { method: 'POST' });
  },
  async trash() {
    return (await api<{ projects: Project[] }>('/api/trash')).projects;
  },
  async emptyTrash() {
    return api<{ removed: number }>('/api/trash', { method: 'DELETE' });
  },
  async purgeProject(id: string) {
    return api(`/api/trash/${id}`, { method: 'DELETE' });
  },
  async sessions(projectId: string) {
    return (await api<{ sessions: Session[] }>(`/api/projects/${projectId}/sessions`)).sessions;
  },
  async createSession(projectId: string, title?: string) {
    return (
      await api<{ session: Session }>(`/api/projects/${projectId}/sessions`, {
        method: 'POST',
        body: JSON.stringify({ title: title || null })
      })
    ).session;
  },
  async renameSession(id: string, title: string) {
    return api<{ session: Session }>(`/api/sessions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title })
    });
  },
  async deleteSession(id: string) {
    return api(`/api/sessions/${id}`, { method: 'DELETE' });
  },
  async messages(sessionId: string, afterSeq?: number) {
    const query = afterSeq ? `?after_seq=${afterSeq}` : '';
    return api<{ session: Session; messages: Message[]; has_more: boolean }>(
      `/api/sessions/${sessionId}/messages${query}`
    );
  },
  async send(sessionId: string, prompt: string) {
    return api<{ job_id: string; message: Message; status: string }>(
      `/api/sessions/${sessionId}/turns`,
      { method: 'POST', body: JSON.stringify({ prompt }) }
    );
  },
  async interrupt(sessionId: string) {
    return api(`/api/sessions/${sessionId}/interrupt`, { method: 'POST' });
  },
  async sessionStatus(sessionId: string) {
    return api<{ running_job_id: string | null; queued_jobs: number }>(
      `/api/sessions/${sessionId}/status`
    );
  },
  async files(projectId: string, path = '') {
    return api<{ path: string; entries: FileEntry[] }>(
      `/api/projects/${projectId}/files?path=${encodeURIComponent(path)}`
    );
  },
  async deleteFile(projectId: string, path: string) {
    return api(`/api/projects/${projectId}/files?path=${encodeURIComponent(path)}`, {
      method: 'DELETE'
    });
  },
  downloadUrl(projectId: string, path: string) {
    return `/api/projects/${projectId}/files/download?path=${encodeURIComponent(path)}`;
  },
  async gitLog(projectId: string) {
    return (await api<{ commits: GitCommit[] }>(`/api/projects/${projectId}/git/log`)).commits;
  },
  async gitShow(projectId: string, hash: string) {
    return api<{ commit: string; stat: string; files: Array<Record<string, string>> }>(
      `/api/projects/${projectId}/git/commit/${encodeURIComponent(hash)}`
    );
  },
  async gitRestore(projectId: string, commit: string) {
    return api(`/api/projects/${projectId}/git/restore`, {
      method: 'POST',
      body: JSON.stringify({ commit })
    });
  },
  async serverInfo() {
    return api<ServerInfo>('/api/server-info');
  }
};

export interface UploadHandle {
  upload_id: string;
  filename: string;
  relative_path: string;
  size: number;
  chunk_size: number;
  total_parts: number;
  received_parts: number;
  expires_at: string;
}

const resumeKey = (projectId: string, file: File) => `ral-upload:${projectId}:${file.name}:${file.size}`;

export async function uploadFile(
  projectId: string,
  file: File,
  onProgress: (sent: number, total: number) => void
): Promise<{ path: string; name: string; size: number }> {
  const cached = localStorage.getItem(resumeKey(projectId, file));
  let uploadId = cached || '';
  let chunkSize = 5 * 1024 * 1024;
  let totalParts = Math.max(1, Math.ceil(file.size / chunkSize));
  let received = 0;
  let relativePath = `uploads/${file.name}`;

  if (uploadId) {
    try {
      const resumed = await api<{
        id: string;
        chunk_size: number;
        total_parts: number;
        received_parts: number;
        relative_path: string;
      }>(`/api/uploads/${uploadId}`);
      chunkSize = resumed.chunk_size;
      totalParts = resumed.total_parts;
      received = resumed.received_parts;
      relativePath = resumed.relative_path;
    } catch {
      localStorage.removeItem(resumeKey(projectId, file));
      uploadId = '';
    }
  }

  if (!uploadId) {
    const init = await api<UploadHandle>(`/api/projects/${projectId}/uploads/init`, {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, size: file.size })
    });
    uploadId = init.upload_id;
    chunkSize = init.chunk_size;
    totalParts = init.total_parts;
    received = init.received_parts;
    relativePath = init.relative_path;
    localStorage.setItem(resumeKey(projectId, file), uploadId);
  }

  onProgress(Math.min(received * chunkSize, file.size), file.size);
  for (let index = received; index < totalParts; index += 1) {
    const start = index * chunkSize;
    const end = Math.min(file.size, start + chunkSize);
    const chunk = file.slice(start, end);
    let attempt = 0;
    while (true) {
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
  const result = await api<{ path: string; name: string; size: number }>(
    `/api/uploads/${uploadId}/complete`,
    { method: 'POST', body: JSON.stringify({}) }
  );
  localStorage.removeItem(resumeKey(projectId, file));
  return result;
}

export type EventHandler = (payload: Record<string, unknown>) => void;

export function subscribeEvents(sessionId: string | null, handlers: Record<string, EventHandler>) {
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
