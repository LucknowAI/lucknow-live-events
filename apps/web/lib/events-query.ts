/** Maps URL search params to backend GET /events query object. */
export function searchParamsToEventQuery(searchParams: URLSearchParams): Record<string, unknown> {
  const o: Record<string, unknown> = {};
  const q = searchParams.get("q");
  if (q) o.q = q;
  const topic = searchParams.get("topic");
  if (topic) o.topic = topic;
  const locality = searchParams.get("locality");
  if (locality) o.locality = locality;
  const community = searchParams.get("community");
  if (community) o.community = community;
  const mode = searchParams.get("mode");
  if (mode) o.mode = mode;
  if (searchParams.get("is_free") === "true") o.is_free = true;
  if (searchParams.get("is_student_friendly") === "true") o.is_student_friendly = true;
  const page = searchParams.get("page");
  if (page) {
    const n = Number(page);
    if (!Number.isNaN(n) && n >= 1) o.page = n;
  }
  return o;
}

export function buildEventsHref(
  base: Record<string, string | undefined>,
  overrides: Record<string, string | undefined | null>,
): string {
  const merged = { ...base, ...overrides };
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(merged)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, v);
  }
  const s = sp.toString();
  return s ? `/events?${s}` : "/events";
}
