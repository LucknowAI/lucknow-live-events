import { ArrowRight, Sparkles } from "lucide-react";
import Link from "next/link";
import { eventService, Event } from "@/lib/api";
import { EventCard } from "@/components/EventCard";

export const revalidate = 60; // SSR with 60s cache

export default async function HomePage() {
  let featuredEvents: Event[] = [];
  let thisWeekEvents: Event[] = [];
  let studentEvents: Event[] = [];

  try {
    featuredEvents = await eventService.getFeatured();
  } catch (e) {
    console.error("Failed to fetch featured events:", e);
  }

  try {
    thisWeekEvents = await eventService.getThisWeek();
  } catch (e) {
    console.error("Failed to fetch this week events:", e);
  }

  try {
    studentEvents = await eventService.getStudentFriendly();
  } catch (e) {
    console.error("Failed to fetch student-friendly events:", e);
  }

  return (
    <div className="flex flex-col gap-16 py-12 px-6">
      {/* Hero Section */}
      <section className="flex flex-col items-center text-center max-w-3xl mx-auto space-y-6">
        <Sparkles className="w-8 h-8 text-primary shadow-primary drop-shadow-md" />
        <h1 className="text-4xl md:text-6xl font-bold tracking-tight text-foreground">
          Lucknow Tech Events
        </h1>
        <p className="text-sm tracking-widest text-primary uppercase font-bold">
          Empowering the Local Community
        </p>
        <p className="text-lg md:text-xl text-muted-foreground mt-2 max-w-2xl">
          The central directory for tech events across Lucknow and Uttar Pradesh. Discover meetups, hackathons, and workshops organized by the community.
        </p>
        <div className="pt-4 flex flex-wrap justify-center gap-4">
          <Link
            href="/events"
            className="inline-flex items-center justify-center rounded-full bg-primary text-primary-foreground px-6 py-3 font-semibold hover:bg-primary/90 transition-colors"
          >
            Browse Events
          </Link>
          <Link
            href="/submit"
            className="inline-flex items-center justify-center rounded-full border border-border bg-transparent text-foreground px-6 py-3 font-semibold hover:bg-muted transition-colors"
          >
            Submit an Event
          </Link>
        </div>
      </section>

      <div className="w-full h-px bg-border max-w-4xl mx-auto" />

      {/* Featured Shelf */}
      <section className="space-y-6 w-full max-w-6xl mx-auto">
        <div className="flex items-center justify-between">
          <h2 className="text-2xl font-bold tracking-tight">Featured Events</h2>
          <Link href="/events" className="text-sm text-primary flex items-center gap-1 hover:underline">
            View all <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
        
        {featuredEvents.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {featuredEvents.map(event => <EventCard key={event.id} event={event} />)}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-border p-12 text-center flex flex-col items-center">
            <p className="text-muted-foreground mb-4">No featured events currently available.</p>
            <div className="flex flex-wrap gap-4 justify-center">
              <Link href="/submit" className="text-primary hover:underline text-sm font-medium">
                Submit an event
              </Link>
              <Link href="/events" className="text-muted-foreground hover:text-primary text-sm font-medium">
                Browse all events →
              </Link>
            </div>
          </div>
        )}
      </section>
      
      {/* This Week Shelf */}
      <section className="space-y-6 w-full max-w-6xl mx-auto pb-12">
        <div className="flex items-center justify-between">
          <h2 className="text-2xl font-bold tracking-tight">This Week</h2>
        </div>
        
        {thisWeekEvents.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {thisWeekEvents.map(event => <EventCard key={event.id} event={event} />)}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-border p-12 text-center flex flex-col items-center gap-4">
            <p className="text-muted-foreground">Nothing scheduled this week yet.</p>
            <div className="flex flex-wrap gap-4 justify-center">
              <Link href="/calendar" className="text-primary text-sm font-semibold hover:underline">
                Open calendar
              </Link>
              <Link href="/events" className="text-muted-foreground text-sm hover:text-primary">
                All events
              </Link>
            </div>
          </div>
        )}
      </section>

      {/* Student-friendly shelf */}
      <section className="space-y-6 w-full max-w-6xl mx-auto pb-12">
        <div className="flex items-center justify-between">
          <h2 className="text-2xl font-bold tracking-tight">Student-friendly & free</h2>
          <Link href="/events?is_student_friendly=true&is_free=true" className="text-sm text-primary hover:underline">
            View filtered list →
          </Link>
        </div>
        {studentEvents.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {studentEvents.map((event) => (
              <EventCard key={event.id} event={event} />
            ))}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-border p-12 text-center">
            <p className="text-muted-foreground mb-4">No student-friendly free events in the next 30 days.</p>
            <Link href="/submit" className="text-primary font-semibold hover:underline text-sm">
              Suggest a college meetup
            </Link>
          </div>
        )}
      </section>
    </div>
  );
}
