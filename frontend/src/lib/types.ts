export type ThemeName = 'blue' | 'mono' | 'orange';

export type JobStatus = 'running' | 'queued' | null;

export interface MenuItem {
  label: string;
  icon?: string;
  danger?: boolean;
  onSelect: () => void;
}

export interface Session {
  id: string;
  project_id: string;
  title: string;
  thread_id?: string | null;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  last_message_seq: number;
  last_read_seq: number;
  unread_count: number;
  last_message?: string | null;
  last_message_status?: string | null;
  job_status?: JobStatus;
  scheduled_pending?: number;
}

export interface ScheduledTask {
  id: string;
  session_id: string;
  title: string;
  prompt: string;
  kind: 'once' | 'daily' | 'weekly' | 'monthly';
  month?: number | null;
  day?: number | null;
  weekday?: number | null;
  hour: number;
  minute: number;
  next_run_at: string;
  enabled: boolean;
  pinned: boolean;
  last_run_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  session_count: number;
  size_bytes: number;
  size_mb: number;
  sessions: Session[];
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
}

export interface ChatImagePayload {
  name: string;
  data_url: string;
}

export interface FileEntry {
  name: string;
  path: string;
  type: 'file' | 'directory';
  size: number;
  mtime: string;
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
  budgets: {
    recommended_parallelism: number;
    available_memory_mb: number;
    free_disk_mb: number;
    heavy_work_allowed: boolean;
    warnings: string[];
  };
}
