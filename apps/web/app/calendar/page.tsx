import { eventService, type Event } from "@/lib/api";
import { CalendarDays, MapPin, Clock } from "lucide-react";
import Link from "next/link";
import { ComingSoonButton } from "@/components/ComingSoonButton";

export const revalidate = 300;

const ICS_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL
    ? `${process.env.NEXT_PUBLIC_BACKEND_URL}/feeds/events.ics`
    : "/api/v1/feeds/events.ics";

interface GroupedDate {
  label: string;
  iso: string;
  events: Event[];
}

function groupByDate(events: Event[]): GroupedDate[] {
  const map: Record<string, { label: string; events: Event[] }> = {};

  for (const event of events) {
    const d = new Date(event.start_at);
    // ISO date key for sorting
    const isoKey = d.toISOString().slice(0, 10); // e.g. "2026-04-30"
    const label = d.toLocaleDateString("en-US", {
      timeZone: "Asia/Kolkata",
      weekday: "short",
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
    // label will look like "Mon, 08 Apr 2026"
    if (!map[isoKey]) map[isoKey] = { label, events: [] };
    map[isoKey].events.push(event);
  }

  // Sort by ISO date ascending
  return Object.entries(map)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([iso, { label, events }]) => ({ iso, label, events }));
}

function formatTime(isoString: string): string {
  return new Date(isoString).toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

function isUpcoming(isoString: string): boolean {
  return new Date(isoString) >= new Date();
}

export default async function CalendarPage() {
  let events: Event[] = [];
  try {
    // Fetch more events and filter to upcoming ones only on the calendar
    const res = await eventService.getEvents({ limit: 200, page: 1 });
    events = res.items.filter((e) => isUpcoming(e.start_at));
    // Sort by start date ascending
    events.sort((a, b) => new Date(a.start_at).getTime() - new Date(b.start_at).getTime());
  } catch (e) {
    console.error("Failed to fetch calendar events:", e);
  }

  const grouped = groupByDate(events);

  return (
    <div className="max-w-4xl mx-auto py-12 px-6">
      <div className="flex items-center justify-between flex-wrap gap-4 mb-16">
        <div className="flex items-center gap-4">
          <CalendarDays className="w-8 h-8 text-primary" />
          <div>
            <h1 className="text-4xl font-extrabold tracking-tight">Timeline</h1>
            <p className="text-sm font-semibold text-muted-foreground mt-1">Upcoming events mapped sequentially</p>
          </div>
        </div>
        <ComingSoonButton className="flex items-center gap-2 rounded-full border border-border bg-card px-5 py-2.5 text-sm font-bold hover:bg-primary hover:text-primary-foreground hover:border-primary transition-all shadow-sm">
          <CalendarDays className="w-4 h-4" /> Subscribe (.ics)
        </ComingSoonButton>
      </div>

      {grouped.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border py-28 text-center flex flex-col items-center bg-card shadow-sm">
          <CalendarDays className="w-16 h-16 text-muted mb-6" />
          <h3 className="text-2xl font-extrabold mb-3">Timeline Empty</h3>
          <p className="text-muted-foreground mb-8 max-w-sm">
            There are no upcoming events scheduled at this moment. 
          </p>
          <Link
            href="/submit"
            className="rounded-full bg-primary text-primary-foreground px-8 py-3 font-bold hover:bg-primary/90 transition-all shadow-lg hover:scale-105"
          >
            Add the next event
          </Link>
        </div>
      ) : (
        <div className="relative pl-6 lg:pl-12">
          {/* Main Vertical Timeline Axis */}
          <div className="absolute left-6 lg:left-12 top-2 bottom-0 w-1 bg-border rounded-full" />

          <div className="space-y-12">
            {grouped.map(({ iso, label, events: dayEvents }, index) => {
              const isFirst = index === 0;

              return (
                <div key={iso} className="relative">
                  {/* Timeline Date Marker (Dot & Label) */}
                  <div className="relative flex items-center mb-6">
                    {/* The Dot */}
                    <div className="absolute -left-2 w-5 h-5 rounded-full bg-background border-4 border-primary z-10 shadow-sm" />
                    
                    <div className="ml-8 flex items-center gap-3">
                      <h3 className="text-lg font-extrabold text-foreground tracking-tight">
                        {label}
                      </h3>
                      {isFirst && (
                        <span className="bg-primary/10 text-primary text-[10px] font-extrabold uppercase px-2 py-0.5 rounded-full tracking-wider">
                          Next Up
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Events nested alongside the axis */}
                  <div className="ml-8 space-y-4">
                    {dayEvents.map((event) => {
                      const registerUrl = event.registration_url || event.canonical_url;
                      const time = formatTime(event.start_at);
                      const endTime = event.end_at ? `– ${formatTime(event.end_at)}` : "";
                      const location =
                        event.mode === "online"
                          ? "Online Stream"
                          : event.venue_name || event.locality || "Location TBA";

                      return (
                        <div
                          key={event.id}
                          className="flex flex-col sm:flex-row sm:items-center gap-4 bg-card border border-border/60 rounded-2xl p-5 hover:border-primary/50 hover:shadow-md transition-all group relative overflow-hidden"
                        >
                          {/* Left Accent line on Hover */}
                          <div className="absolute left-0 top-0 bottom-0 w-1 bg-primary transform -translate-x-full group-hover:translate-x-0 transition-transform" />

                          {/* Time Column */}
                          <div className="w-auto sm:w-28 flex-shrink-0">
                            <div className="flex sm:flex-col gap-2 sm:gap-0.5 items-center sm:items-start text-sm">
                              <span className="font-extrabold text-primary flex items-center gap-1.5">
                                <Clock className="w-3.5 h-3.5" />
                                {time}
                              </span>
                              {endTime && (
                                <span className="text-muted-foreground font-semibold text-xs ml-5 sm:ml-0">{endTime}</span>
                              )}
                            </div>
                          </div>

                          <div className="hidden sm:block w-px h-12 bg-border/50 flex-shrink-0" />

                          {/* Info Column */}
                          <div className="flex-1 min-w-0">
                            <a
                              href={registerUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-lg font-bold text-foreground group-hover:text-primary transition-colors block truncate pr-4"
                            >
                              {event.title}
                            </a>
                            <div className="flex flex-wrap items-center gap-y-1 gap-x-3 mt-1.5 text-xs font-semibold text-muted-foreground">
                              <span className="flex items-center gap-1 text-foreground/70">
                                <MapPin className="w-3.5 h-3.5 flex-shrink-0" />
                                {location}
                              </span>
                              {event.community_name && (
                                <span className="opacity-80 flex items-center gap-1.5 before:content-['•'] before:opacity-30 before:mr-0.5">
                                  {event.community_name}
                                </span>
                              )}
                            </div>
                          </div>

                          {/* Badges Column */}
                          <div className="flex sm:flex-col lg:flex-row items-center sm:items-end lg:items-center gap-2.5 flex-shrink-0 mt-3 sm:mt-0">
                            {event.is_free && (
                              <span className="text-[10px] font-extrabold uppercase tracking-wider text-primary bg-primary/10 px-2.5 py-1 rounded-full">
                                Free
                              </span>
                            )}
                            <a
                              href={registerUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs font-bold bg-foreground text-background rounded-full px-4 py-2 hover:bg-primary hover:text-primary-foreground hover:shadow-lg transition-all whitespace-nowrap"
                            >
                              View & Register
                            </a>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            
            {/* End of Timeline marker */}
            <div className="relative flex items-center pt-4">
              <div className="absolute -left-2 w-5 h-5 rounded-full bg-border border-4 border-background z-10" />
              <div className="ml-8 text-xs font-bold text-muted-foreground tracking-widest uppercase">
                End of Schedule
              </div>
            </div>
            
          </div>
        </div>
      )}
    </div>
  );
}
