export interface Project {
  id: string;
  name: string;
  slug: string;
  status: 'active' | 'trashed';
  created_at: string;
  updated_at: string;
  trashed_at?: string | null;
  purge_at?: string | null;
  session_count?: number;
  size_bytes?: number;
  size_mb?: number;
}

export interface Session {
  id: string;
  project_id: string;
  title: string;
  thread_id?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  last_message_seq: number;
  last_read_seq: number;
  unread_count?: number;
  last_message?: string | null;
  last_message_status?: string | null;
}

export interface Message {
  id: string;
  session_id: string;
  seq: number;
  role: 'user' | 'assistant' | 'system';
  content: string;
  status: string;
  job_id?: string | null;
  created_at: string;
  completed_at?: string | null;
  error?: string | null;
  streaming?: boolean;
}

export interface FileEntry {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size: number;
  mtime: string;
}

export interface GitCommit {
  hash: string;
  timestamp: number;
  subject: string;
}

export interface ServerInfo {
  hostname: string;
  platform: string;
  cpu: { count: number; load_1m: number; load_5m: number; load_15m: number };
  memory: {
    total_mb: number;
    available_mb: number;
    cgroup_current_mb?: number | null;
    cgroup_max_mb?: number | null;
  };
  disk: { free_mb: number; total_mb: number };
  processes: Array<{ pid: number; rss_mb: number; command: string; same_user?: boolean }>;
  budgets: {
    recommended_parallelism: number;
    available_memory_mb: number;
    free_disk_mb: number;
    heavy_work_allowed: boolean;
    warnings: string[];
  };
}

