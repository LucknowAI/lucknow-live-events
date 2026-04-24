import type { Event } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { ArrowUpRight, MapPin, Calendar } from "lucide-react";
import Image from "next/image";

const topicLabel = (t: unknown): string =>
  typeof t === "string" ? t : (t as any)?.name ?? String(t);

export function EventCard({ event, isPast = false }: { event: Event; isPast?: boolean }) {
  const targetUrl = event.registration_url || event.canonical_url;

  return (
    <a
      href={targetUrl}
      target="_blank"
      rel="noopener noreferrer"
      className={`flex flex-col overflow-hidden rounded-xl border border-border bg-card transition-all group h-full ${
        isPast
          ? "opacity-55 grayscale-[60%] cursor-default hover:opacity-65"
          : "hover:border-primary/50 hover:shadow-lg hover:-translate-y-0.5"
      }`}
      title={event.title}
    >
      {event.poster_url ? (
        <div className="h-40 w-full overflow-hidden border-b border-border">
          <Image
            src={event.poster_url}
            alt={event.title}
            width={1200}
            height={600}
            className="h-full w-full object-cover transition-transform group-hover:scale-105"
          />
        </div>
      ) : (
        <div className="h-40 w-full border-b border-border bg-gradient-to-br from-background to-card flex items-center justify-center p-6 text-center">
          <span className="text-xl font-bold tracking-tight text-primary/80 line-clamp-2">{event.title}</span>
        </div>
      )}

      <div className="flex flex-1 flex-col p-5">
        <div className="mb-3 flex items-center gap-2 flex-wrap">
          {event.event_type && (
            <span className="inline-flex rounded-full border border-border bg-background px-2 py-0.5 text-xs font-semibold text-muted-foreground capitalize">
              {event.event_type}
            </span>
          )}
          {event.mode && (
            <span className="inline-flex rounded-full border border-border bg-muted/50 px-2 py-0.5 text-xs font-semibold text-muted-foreground capitalize">
              {event.mode === "offline" ? "In-Person" : event.mode === "online" ? "Online" : "Hybrid"}
            </span>
          )}
        </div>

        {event.community_name && (
          <div className="flex items-center gap-2 mb-1 text-xs font-bold text-primary/80 uppercase tracking-wider">
            <span className="truncate">{event.community_name}</span>
          </div>
        )}

        <p className="mb-2 text-base font-bold leading-tight text-foreground line-clamp-2 group-hover:text-primary transition-colors">
          {event.title}
        </p>

        <div className="mb-4 space-y-1.5 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <Calendar className="h-3.5 w-3.5 shrink-0" />
            {(event as any).date_tba ? (
              <span className="inline-flex items-center gap-1 text-xs font-bold text-yellow-600 bg-yellow-500/10 border border-yellow-500/20 rounded-full px-2 py-0.5">
                📅 Date TBA
              </span>
            ) : (
              <span className={`truncate${isPast ? " line-through text-muted-foreground/60" : ""}`}>
                {formatDate(event.start_at)}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <MapPin className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">
              {event.mode === "online"
                ? "Online"
                : [event.venue_name, event.locality].filter(Boolean).join(", ") || "Location TBA"}
            </span>
          </div>
        </div>

        {event.topics && event.topics.length > 0 && (
          <div className="mb-4 flex flex-wrap gap-1.5">
            {event.topics.slice(0, 3).map((topic, i) => (
              <span key={i} className="inline-flex items-center rounded-md bg-muted px-2 py-1 text-[10px] font-medium text-muted-foreground">
                {topicLabel(topic)}
              </span>
            ))}
            {event.topics.length > 3 && (
              <span className="inline-flex items-center rounded-md bg-muted px-2 py-1 text-[10px] font-medium text-muted-foreground">
                +{event.topics.length - 3}
              </span>
            )}
          </div>
        )}

        <div className="mt-auto pt-4 flex gap-2 w-full justify-between items-center border-t border-border/50">
          {isPast ? (
            <span className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg bg-muted text-muted-foreground px-4 py-2 text-sm font-semibold">
              ✓ Completed
            </span>
          ) : (
            <span className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg bg-primary text-primary-foreground px-4 py-2 text-sm font-semibold transition-colors group-hover:bg-primary/90">
              View Event <ArrowUpRight className="h-4 w-4" />
            </span>
          )}
        </div>
      </div>
    </a>
  );
}
