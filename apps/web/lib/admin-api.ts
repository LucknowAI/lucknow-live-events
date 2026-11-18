import axios from "axios";

const DEFAULT = "http://localhost:8000/api/v1";

function baseUrl(): string {
  // Browser: relative path goes through Next.js rewrite proxy
  if (typeof window !== "undefined") return "/api/v1";
  // SSR: Docker dev uses INTERNAL_API_URL, production uses NEXT_PUBLIC_API_URL
  return (
    process.env.INTERNAL_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    DEFAULT
  );
}

export const adminApi = axios.create({
  baseURL: baseUrl(),
  headers: { "Content-Type": "application/json" },
});

adminApi.interceptors.request.use((config) => {
  if (typeof window === "undefined") return config;
  const t = localStorage.getItem("admin_token");
  if (t) config.headers.Authorization = `Bearer ${t}`;
  return config;
});

// ─── Types ────────────────────────────────────────────────────────────────────

export interface AdminSource {
  id: string;
  name: string;
  platform: string | null;
  base_url: string;
  enabled: boolean;
  status: "active" | "whitelisted" | "blacklisted";
  crawl_strategy: string | null;
  trust_score: number;
  crawl_interval_hours: number;
  last_crawled_at: string | null;
  last_success_at: string | null;
  consecutive_failures: number;
  created_at: string;
}

export interface AdminEvent {
  id: string;
  slug: string;
  title: string;
  start_at: string;
  end_at: string | null;
  mode: string | null;
  event_type: string | null;
  city: string | null;
  locality: string | null;
  venue_name: string | null;
  community_name: string | null;
  canonical_url: string;
  registration_url: string | null;
  poster_url: string | null;
  is_featured: boolean;
  is_cancelled: boolean;
  is_free: boolean;
  topics_json: string[];
  published_at: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdminEventListResponse {
  items: AdminEvent[];
  page: number;
  limit: number;
  total: number;
}

export interface EventUpdate {
  title?: string;
  description?: string;
  short_description?: string;
  start_at?: string;
  end_at?: string;
  city?: string;
  locality?: string;
  venue_name?: string;
  mode?: string;
  event_type?: string;
  canonical_url?: string;
  registration_url?: string;
  poster_url?: string;
  is_free?: boolean;
  is_featured?: boolean;
  is_cancelled?: boolean;
}

export interface StatsOut {
  events_total: number;
  events_this_week: number;
  pending_moderation: number;
  sources_active: number;
  sources_blacklisted: number;
}

export interface CommunitySubmission {
  id: string;
  community_name: string | null;
  community_url: string | null;
  community_description: string | null;
  submitter_name: string | null;
  submitter_email: string | null;
  notes: string | null;
  status: string;
  created_at: string;
}

export const adminService = {
  // Auth
  login: async (email: string, password: string): Promise<{ access_token: string }> => {
    const { data } = await adminApi.post<{ access_token: string }>("/admin/auth/login", { email, password });
    return data;
  },

  // Stats
  stats: async (): Promise<StatsOut> => {
    const { data } = await adminApi.get<StatsOut>("/admin/stats");
    return data;
  },

  // Sources
  listSources: async (): Promise<AdminSource[]> => {
    const { data } = await adminApi.get<AdminSource[]>("/admin/sources");
    return data;
  },
  createSource: async (payload: Partial<AdminSource> & { base_url: string; name: string }): Promise<AdminSource> => {
    const { data } = await adminApi.post<AdminSource>("/admin/sources", payload);
    return data;
  },
  patchSource: async (id: string, body: Partial<AdminSource>): Promise<AdminSource> => {
    const { data } = await adminApi.patch<AdminSource>(`/admin/sources/${id}`, body);
    return data;
  },
  setSourceStatus: async (id: string, status: "active" | "whitelisted" | "blacklisted"): Promise<AdminSource> => {
    const { data } = await adminApi.post<AdminSource>(`/admin/sources/${id}/status`, { status });
    return data;
  },
  deleteSource: async (id: string): Promise<void> => {
    await adminApi.delete(`/admin/sources/${id}`);
  },

  // Events
  listEvents: async (page = 1, limit = 50, q?: string): Promise<AdminEventListResponse> => {
    const params = new URLSearchParams({ page: String(page), limit: String(limit) });
    if (q) params.set("q", q);
    const { data } = await adminApi.get<AdminEventListResponse>(`/admin/events?${params}`);
    return data;
  },
  updateEvent: async (id: string, payload: EventUpdate): Promise<AdminEvent> => {
    const { data } = await adminApi.put<AdminEvent>(`/admin/events/${id}`, payload);
    return data;
  },
  featureEvent: async (id: string, featured: boolean): Promise<AdminEvent> => {
    const { data } = await adminApi.patch<AdminEvent>(`/admin/events/${id}/feature`, null, {
      params: { featured },
    });
    return data;
  },
  cancelEvent: async (id: string): Promise<AdminEvent> => {
    const { data } = await adminApi.patch<AdminEvent>(`/admin/events/${id}/cancel`);
    return data;
  },
  deleteEvent: async (id: string): Promise<void> => {
    await adminApi.delete(`/admin/events/${id}`);
  },

  // Moderation — community submissions
  listCommunitySubmissions: async (status = "pending"): Promise<CommunitySubmission[]> => {
    const { data } = await adminApi.get<CommunitySubmission[]>(`/admin/moderation/communities?status=${status}`);
    return data;
  },
  approveModeration: async (id: string): Promise<Record<string, unknown>> => {
    const { data } = await adminApi.post<Record<string, unknown>>(`/admin/moderation/communities/${id}/approve`);
    return data;
  },
  rejectModeration: async (id: string): Promise<Record<string, unknown>> => {
    const { data } = await adminApi.post<Record<string, unknown>>(`/admin/moderation/communities/${id}/reject`);
    return data;
  },
};
