import { Event } from "@/lib/api";

export function EventJsonLd({ event, siteUrl }: { event: Event; siteUrl: string }) {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "Event",
    name: event.title,
    startDate: event.start_at,
    endDate: event.end_at || event.start_at,
    eventAttendanceMode:
      event.mode === "online"
        ? "https://schema.org/OnlineEventAttendanceMode"
        : event.mode === "hybrid"
        ? "https://schema.org/MixedEventAttendanceMode"
        : "https://schema.org/OfflineEventAttendanceMode",
    eventStatus: "https://schema.org/EventScheduled",
    location: {
      "@type": event.mode === "online" ? "VirtualLocation" : "Place",
      name: event.venue_name || event.locality || "Lucknow",
      url: event.mode === "online" ? event.registration_url : undefined,
    },
    image: event.poster_url ? [event.poster_url] : undefined,
    description: event.short_description || event.description || event.title,
    isAccessibleForFree: event.is_free,
    organizer: event.community_name
      ? {
          "@type": "Organization",
          name: event.community_name,
        }
      : undefined,
    url: `${siteUrl.replace(/\/$/, "")}/events/${event.slug}`,
  };

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
    />
  );
}
