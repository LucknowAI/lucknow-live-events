import type { Event } from "@/lib/api";

function toGCalUtc(iso: string): string {
  const d = new Date(iso);
  return d.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
}

/** End time for calendar: use event end or +1h after start. */
function endIso(start: string, end: string | null): string {
  if (end) return end;
  const s = new Date(start);
  s.setHours(s.getHours() + 1);
  return s.toISOString();
}

/** Opens Google Calendar prefilled “create event” for this listing. */
export function googleCalendarTemplateUrl(event: Event): string {
  const start = toGCalUtc(event.start_at);
  const end = toGCalUtc(endIso(event.start_at, event.end_at));
  const location =
    event.mode === "online"
      ? "Online"
      : [event.venue_name, event.locality].filter(Boolean).join(", ") || "Lucknow, India";
  const details = [event.short_description, event.canonical_url].filter(Boolean).join("\n\n");
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: event.title,
    dates: `${start}/${end}`,
    details: details || event.canonical_url,
    location,
  });
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}
