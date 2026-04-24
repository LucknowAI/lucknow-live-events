const DEFAULT_API_URL = "http://localhost:8000/api/v1";

function resolveBaseUrl(): string {
  // Browser: always use a relative path.
  // The Next.js rewrite (next.config.js) proxies /api/v1/* to the real backend.
  // This works for both Docker dev and Vercel+Cloud Run without any changes here.
  if (typeof window !== "undefined") return "/api/v1";

  // Server-side (SSR / RSC / build-time):
  //   Docker dev   → INTERNAL_API_URL=http://api:8000/api/v1 (set by docker-compose)
  //   Production   → NEXT_PUBLIC_API_URL=https://<cloud-run>/api/v1 (set in Vercel dashboard)
  //   Plain local  → falls back to localhost
  const url =
    process.env.INTERNAL_API_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    DEFAULT_API_URL;

  if (url === DEFAULT_API_URL) {
    console.warn(
      "[api] Neither INTERNAL_API_URL nor NEXT_PUBLIC_API_URL is set. " +
        "Falling back to localhost — this will fail in production."
    );
  }

  return url;
}

export interface Event {
  id: string;
  slug: string;
  title: string;
  start_at: string;
  end_at: string | null;
  venue_name: string | null;
  locality: string | null;
  mode: "offline" | "online" | "hybrid" | string | null;
  event_type: string | null;
  community_name: string | null;
  organizer_name: string | null;
  poster_url: string | null;
  canonical_url: string;
  registration_url: string | null;
  is_free: boolean;
  is_student_friendly: boolean;
  topics: string[];
  audience?: string[];
  short_description?: string | null;
  description?: string | null;
  updated_at?: string;
  date_tba?: boolean;
}

export interface EventsResponse {
  items: Event[];
  total: number;
  page: number;
  limit: number;
}

export interface FacetItem {
  name: string;
  count: number;
}

export interface FacetsResponse {
  items: FacetItem[];
}

async function fetchApi<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const baseUrl = resolveBaseUrl();
  const url = `${baseUrl}${endpoint}`;
  
  const headers = new Headers(options.headers || {});
  headers.set("Content-Type", "application/json");

  const response = await fetch(url, {
    ...options,
    headers,
    // Add cache revalidation strategy
    next: { revalidate: 60 }
  });

  if (!response.ok) {
    throw new Error(`API GET Error: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

export const fetcher = async (url: string) => {
  // fetcher is mostly used by SWR on the client side with relative paths
  // SWR already passes the full URL from the hook
  const prefix = url.startsWith("http") || url.startsWith("/") ? "" : resolveBaseUrl();
  const res = await fetch(`${prefix}${url}`, {
    headers: { "Content-Type": "application/json" }
  });
  if (!res.ok) throw new Error("API Error");
  return res.json();
};

export const facetService = {
  getTopics: async (): Promise<FacetItem[]> => {
    const data = await fetchApi<FacetsResponse>("/topics");
    return data.items;
  },
  getCommunities: async (): Promise<FacetItem[]> => {
    const data = await fetchApi<FacetsResponse>("/communities");
    return data.items;
  },
  getLocalities: async (): Promise<FacetItem[]> => {
    const data = await fetchApi<FacetsResponse>("/localities");
    return data.items;
  },
};

export const eventService = {
  getEvents: async (params?: Record<string, unknown>): Promise<EventsResponse> => {
    const query = params ? "?" + new URLSearchParams(params as Record<string, string>).toString() : "";
    return fetchApi<EventsResponse>(`/events${query}`, { cache: 'no-store' }); // Always get fresh list
  },

  getEvent: async (slug: string): Promise<Event> => {
    return fetchApi<Event>(`/events/${slug}`);
  },

  getFeatured: async (): Promise<Event[]> => {
    return fetchApi<Event[]>("/events/featured");
  },

  getThisWeek: async (): Promise<Event[]> => {
    return fetchApi<Event[]>("/events/this-week");
  },

  getStudentFriendly: async (): Promise<Event[]> => {
    return fetchApi<Event[]>("/events/student-friendly");
  },

  getPastEvents: async (days = 30): Promise<Event[]> => {
    return fetchApi<Event[]>(`/events/past?days=${days}`);
  },

  submitEvent: async (payload: { url: string }): Promise<void> => {
    await fetchApi<void>("/submissions", {
      method: "POST",
      body: JSON.stringify({ event_url: payload.url }),
    });
  },
};
