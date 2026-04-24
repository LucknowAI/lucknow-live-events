import { EventJsonLd } from "@/components/EventJsonLd";
import { eventService } from "@/lib/api";
import { googleCalendarTemplateUrl } from "@/lib/calendar-links";
import { formatDate } from "@/lib/utils";
import { ArrowLeft, ArrowRight, Calendar as CalendarIcon, MapPin, Globe } from "lucide-react";
import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";

export const revalidate = 3600;

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";

const topicLabel = (t: unknown): string =>
  typeof t === "string" ? t : (t as any)?.name ?? String(t);


export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  try {
    const event = await eventService.getEvent(slug);
    const title = `${event.title} | Lucknow Tech Events`;
    const description =
      event.short_description?.trim() ||
      (event.description ? event.description.replace(/<[^>]+>/g, "").slice(0, 160) : null) ||
      `Tech event in Lucknow — ${event.event_type || "meetup"}.`;
    return {
      title,
      description,
      openGraph: {
        title,
        description,
        url: `${siteUrl.replace(/\/$/, "")}/events/${event.slug}`,
        siteName: "Lucknow Tech Events",
        type: "website",
        images: event.poster_url ? [{ url: event.poster_url, alt: event.title }] : undefined,
      },
      twitter: {
        card: event.poster_url ? "summary_large_image" : "summary",
        title,
        description,
        images: event.poster_url ? [event.poster_url] : undefined,
      },
    };
  } catch {
    return { title: "Event | Lucknow Tech Events" };
  }
}

export default async function EventDetailPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;

  let event;
  try {
    event = await eventService.getEvent(slug);
  } catch {
    notFound();
  }

  if (!event) notFound();

  const registerUrl = event.registration_url || event.canonical_url;

  return (
    <>
      <EventJsonLd event={event} siteUrl={siteUrl} />
      <div className="max-w-5xl mx-auto py-8 px-6 lg:py-12">
        <Link
          href="/events"
          className="inline-flex items-center gap-2 text-sm text-muted-foreground mb-8 hover:text-primary transition-colors font-medium"
        >
          <ArrowLeft className="w-4 h-4" /> Back to events
        </Link>

        <div className="bg-card border border-border rounded-2xl overflow-hidden shadow-2xl">
          {event.poster_url && (
            <div className="w-full h-64 md:h-96 relative border-b border-border bg-gradient-to-tr from-background to-card">
              <Image
                src={event.poster_url}
                alt={event.title}
                fill
                sizes="(max-width: 768px) 100vw, 1024px"
                className="object-contain mix-blend-plus-lighter"
                priority
              />
            </div>
          )}

          <div className="p-8 md:p-12 relative">
            <div className="flex flex-wrap items-center gap-3 mb-6">
              <span className="inline-flex rounded-full border border-border bg-background px-3 py-1 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                {event.event_type}
              </span>
              {event.is_free && (
                <span className="inline-flex rounded-full bg-primary/10 text-primary px-3 py-1 text-xs font-bold uppercase tracking-wider">
                  FREE
                </span>
              )}
              {event.is_student_friendly && (
                <span className="inline-flex rounded-full border border-border bg-background px-3 py-1 text-xs font-bold uppercase tracking-wider text-muted-foreground">
                  STUDENT
                </span>
              )}
              <span className="inline-flex rounded-full border border-border bg-muted/50 px-3 py-1 text-xs font-bold tracking-wider text-foreground">
                {event.mode}
              </span>
            </div>

            <h1 className="text-4xl md:text-5xl font-extrabold tracking-tight mb-8 text-foreground leading-[1.1]">
              {event.title}
            </h1>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-12 mb-10">
              <div className="space-y-8">
                <div className="flex gap-4 items-start">
                  <div className="bg-secondary p-4 rounded-xl text-primary mt-1 shadow-inner">
                    <CalendarIcon className="w-6 h-6" />
                  </div>
                  <div>
                    <h3 className="font-bold tracking-wide text-foreground uppercase text-xs mb-1 text-muted-foreground">
                      Date & Time
                    </h3>
                    <p className="text-lg font-medium text-foreground">{formatDate(event.start_at)}</p>
                    {event.end_at && (
                      <p className="text-muted-foreground">to {formatDate(event.end_at)}</p>
                    )}
                  </div>
                </div>

                <div className="flex gap-4 items-start">
                  <div className="bg-secondary p-4 rounded-xl text-primary mt-1 shadow-inner">
                    <MapPin className="w-6 h-6" />
                  </div>
                  <div>
                    <h3 className="font-bold tracking-wide text-foreground uppercase text-xs mb-1 text-muted-foreground">
                      Location
                    </h3>
                    <p className="text-lg font-medium text-foreground capitalize">
                      {event.venue_name || event.locality || "TBD"}
                    </p>
                  </div>
                </div>
              </div>

              <div className="space-y-6">
                {event.community_name && (
                  <div className="flex gap-4 items-start">
                    <div className="bg-secondary p-4 rounded-xl text-primary mt-1 shadow-inner">
                      <Globe className="w-6 h-6" />
                    </div>
                    <div>
                      <h3 className="font-bold tracking-wide text-foreground uppercase text-xs mb-1 text-muted-foreground">
                        Community / Organizer
                      </h3>
                      <p className="text-lg font-medium text-foreground">{event.community_name}</p>
                      <p className="text-muted-foreground">{event.organizer_name}</p>
                    </div>
                  </div>
                )}

                <div className="bg-background rounded-2xl border border-border p-8 text-center mt-4 space-y-4">
                  <h3 className="font-bold mb-2 text-foreground tracking-tight text-xl">Ready to join?</h3>
                  <a
                    href={registerUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="w-full flex items-center justify-center gap-2 rounded-xl bg-primary text-primary-foreground px-8 py-4 font-bold text-lg transition-all hover:bg-primary/90 hover:scale-[1.02] active:scale-95 shadow-lg shadow-primary/20"
                  >
                    Register Now <ArrowRight className="h-5 w-5" />
                  </a>
                  <a
                    href={googleCalendarTemplateUrl(event)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="w-full flex items-center justify-center gap-2 rounded-xl border border-border bg-card text-foreground px-6 py-3 text-sm font-semibold transition-colors hover:border-primary hover:text-primary"
                  >
                    Add to Google Calendar
                  </a>
                </div>
              </div>
            </div>

            <div className="w-full h-px bg-border my-12" />

            <div className="prose prose-invert max-w-none">
              {event.topics && event.topics.length > 0 && (
                <div className="mt-8">
                  <h3 className="text-2xl font-bold tracking-tight mb-6 text-foreground">Topics covered</h3>
                  <div className="flex flex-wrap gap-2">
                    {event.topics.map((topic) => (
                      <span
                        key={topicLabel(topic)}
                        className="px-4 py-2 bg-secondary text-secondary-foreground border border-border shadow-inner rounded-full text-sm font-semibold tracking-wide"
                      >
                        {topicLabel(topic)}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
