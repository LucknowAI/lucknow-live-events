"use client";

import { EventCard } from "@/components/EventCard";
import { EventGridSkeleton } from "@/components/EventGridSkeleton";
import { eventService, facetService, type EventsResponse, type FacetItem } from "@/lib/api";
import { buildEventsHref, eventStatusFromSearchParams, searchParamsToEventQuery } from "@/lib/events-query";
import { ChevronLeft, ChevronRight, Loader2, Search, SlidersHorizontal, ChevronDown, MapPin, Users, Tag, MonitorPlay } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback } from "react";
import useSWR from "swr";

const emptyResponse: EventsResponse = { items: [], total: 0, page: 1, limit: 20 };
const views = [
  { status: "upcoming", label: "Upcoming", empty: "No upcoming events" },
  { status: "ongoing", label: "Happening now", empty: "No events happening now" },
  { status: "past", label: "Past", empty: "No events in the past 30 days" },
] as const;

async function fetchEvents(qs: string): Promise<EventsResponse> {
  const params = searchParamsToEventQuery(new URLSearchParams(qs));
  return eventService.getEvents(params);
}

export function EventsExplorer() {
  const searchParams = useSearchParams();
  const queryKey = searchParams.toString();
  const status = eventStatusFromSearchParams(searchParams);
  const view = views.find((item) => item.status === status) ?? views[0];

  const { data, error, isLoading, isValidating, mutate } = useSWR(
    ["events", queryKey],
    ([, qs]) => fetchEvents(qs),
    {
      revalidateOnFocus: false,
      keepPreviousData: false,
    },
  );

  const { data: topics = [] } = useSWR<FacetItem[]>("facets-topics", () => facetService.getTopics(), {
    revalidateOnFocus: false,
  });
  const { data: localities = [] } = useSWR<FacetItem[]>(
    "facets-localities",
    () => facetService.getLocalities(),
    { revalidateOnFocus: false },
  );
  const { data: communities = [] } = useSWR<FacetItem[]>(
    "facets-communities",
    () => facetService.getCommunities(),
    { revalidateOnFocus: false },
  );

  const response = data ?? emptyResponse;
  const { items, total, page, limit } = response;
  const totalPages = Math.max(1, Math.ceil(total / limit));

  const filterBase: Record<string, string | undefined> = Object.fromEntries(searchParams.entries());
  const hasActiveFilters = Array.from(searchParams.keys()).some((key) => key !== "status");

  const replaceQuery = useCallback(
    (mutate: (sp: URLSearchParams) => void) => {
      const sp = new URLSearchParams(window.location.search);
      mutate(sp);
      sp.delete("page");
      const s = sp.toString();
      // Filters are client-only; native history keeps useSearchParams in sync.
      window.history.replaceState(null, "", s ? `/events?${s}` : "/events");
    },
    [],
  );

  const clearFilters = () => replaceQuery((sp) => {
    Array.from(sp.keys()).forEach((key) => { if (key !== "status") sp.delete(key); });
  });

  const applySearch = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const q = String(fd.get("q") ?? "").trim();
    replaceQuery((sp) => {
      if (q) sp.set("q", q);
      else sp.delete("q");
    });
  };

  const onFilterChange = (key: string, value: string) => {
    replaceQuery((sp) => {
      if (value) sp.set(key, value);
      else sp.delete(key);
    });
  };


  const prevHref =
    page > 1 ? buildEventsHref(filterBase, { page: page > 2 ? String(page - 1) : null }) : null;
  const nextHref =
    page < totalPages ? buildEventsHref(filterBase, { page: String(page + 1) }) : null;

  const currentTopic = searchParams.get("topic") ?? "";
  const currentLocality = searchParams.get("locality") ?? "";
  const currentCommunity = searchParams.get("community") ?? "";
  const currentMode = searchParams.get("mode") ?? "";

  return (
    <div className="flex flex-col min-h-full bg-muted/10">
      {/* Top Sticky Filter Bar */}
      <div className="sticky top-0 z-20 bg-background/80 backdrop-blur-xl border-b border-border shadow-sm">
        <form onSubmit={applySearch} className="px-6 lg:px-10 py-4 max-w-screen-2xl mx-auto w-full">
          {/* Main Filter Row */}
          <div className="flex flex-col lg:flex-row gap-4 items-stretch lg:items-center">
            
            {/* Search Bar - Flex 1 to take remaining space */}
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-3.5 top-3 h-4 w-4 text-muted-foreground transition-colors group-focus-within:text-primary" />
              <input
                key={queryKey}
                name="q"
                defaultValue={searchParams.get("q") ?? ""}
                placeholder="Search events, keywords..."
                className="w-full h-10 rounded-full border border-border bg-card py-2 pl-10 pr-4 text-sm font-medium focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary shadow-sm transition-all"
              />
            </div>

            {/* Dropdown Pills Area */}
            <div className="flex items-center gap-3 overflow-x-auto pb-2 lg:pb-0 scrollbar-hide flex-shrink-0">
              
              {/* Topic Dropdown */}
              <div className="relative flex-shrink-0">
                <select
                  value={currentTopic}
                  onChange={(e) => onFilterChange("topic", e.target.value)}
                  className="appearance-none h-10 rounded-full border border-border bg-card py-2 pl-10 pr-8 text-sm font-bold hover:bg-muted/50 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary shadow-sm transition-all cursor-pointer min-w-[140px]"
                >
                  <option value="">Any Topic</option>
                  {topics.filter((t) => t.count && t.count > 0).map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name}
                    </option>
                  ))}
                </select>
                <Tag className="absolute left-3.5 top-3 h-4 w-4 text-muted-foreground pointer-events-none" />
                <ChevronDown className="absolute right-3.5 top-3 h-4 w-4 text-muted-foreground pointer-events-none" />
              </div>

              {/* Community Dropdown */}
              <div className="relative flex-shrink-0">
                <select
                  value={currentCommunity}
                  onChange={(e) => onFilterChange("community", e.target.value)}
                  className="appearance-none h-10 rounded-full border border-border bg-card py-2 pl-10 pr-8 text-sm font-bold hover:bg-muted/50 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary shadow-sm transition-all cursor-pointer min-w-[160px]"
                >
                  <option value="">Any Community</option>
                  {communities.map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name}
                    </option>
                  ))}
                </select>
                <Users className="absolute left-3.5 top-3 h-4 w-4 text-muted-foreground pointer-events-none" />
                <ChevronDown className="absolute right-3.5 top-3 h-4 w-4 text-muted-foreground pointer-events-none" />
              </div>

              {/* Mode Dropdown */}
              <div className="relative flex-shrink-0">
                <select
                  value={currentMode}
                  onChange={(e) => onFilterChange("mode", e.target.value)}
                  className="appearance-none h-10 rounded-full border border-border bg-card py-2 pl-10 pr-8 text-sm font-bold hover:bg-muted/50 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary shadow-sm transition-all cursor-pointer min-w-[130px]"
                >
                  <option value="">Any Mode</option>
                  <option value="offline">In-Person</option>
                  <option value="online">Online</option>
                  <option value="hybrid">Hybrid</option>
                </select>
                <MonitorPlay className="absolute left-3.5 top-3 h-4 w-4 text-muted-foreground pointer-events-none" />
                <ChevronDown className="absolute right-3.5 top-3 h-4 w-4 text-muted-foreground pointer-events-none" />
              </div>

              {/* Location Dropdown */}
              <div className="relative flex-shrink-0">
                <select
                  value={currentLocality}
                  onChange={(e) => onFilterChange("locality", e.target.value)}
                  disabled={currentMode === "online"}
                  className="appearance-none h-10 rounded-full border border-border bg-card py-2 pl-10 pr-8 text-sm font-bold hover:bg-muted/50 focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary shadow-sm transition-all cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed min-w-[150px]"
                >
                  <option value="">Any Location</option>
                  {localities.map((l) => (
                    <option key={l.name} value={l.name}>
                      {l.name}
                    </option>
                  ))}
                </select>
                <MapPin className="absolute left-3.5 top-3 h-4 w-4 text-muted-foreground pointer-events-none" />
                <ChevronDown className="absolute right-3.5 top-3 h-4 w-4 text-muted-foreground pointer-events-none" />
              </div>

              {/* Mobile Submit Button (Hidden on wide screens as live search handles drops) */}
              <button
                type="submit"
                className="lg:hidden h-10 px-6 rounded-full bg-primary text-primary-foreground text-sm font-bold shadow-md flex-shrink-0"
              >
                Go
              </button>
            </div>
          </div>

          <nav aria-label="Event views" className="mt-4 flex gap-2">
            {views.map((item) => (
              <Link key={item.status}
                href={buildEventsHref(filterBase, { status: item.status, page: null })}
                scroll={false} aria-current={status === item.status ? "page" : undefined}
                className={`rounded-full px-4 py-2 text-sm font-bold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${status === item.status ? "bg-primary text-primary-foreground" : "border border-border hover:bg-muted"}`}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="flex items-center justify-end mt-4">
            <div className="flex items-center gap-4">
              {hasActiveFilters && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="text-[13px] font-bold text-muted-foreground hover:text-destructive transition-colors hidden sm:block"
                >
                  Clear Filters
                </button>
              )}
              <div className="flex items-center gap-2">
                {isValidating && <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />}
                <span aria-live="polite" className="text-[13px] font-bold text-muted-foreground uppercase tracking-wider">{error ? "Results unavailable" : isLoading ? "Loading events" : `${total} results`}</span>
              </div>
            </div>
          </div>
        </form>
      </div>

      {/* Main Grid Area */}
      <div aria-busy={isLoading || isValidating} className="flex-1 w-full max-w-screen-2xl mx-auto p-6 lg:p-10">
        {error && (
          <div role="alert" className="bg-destructive/10 border border-destructive/20 text-foreground text-sm font-medium rounded-xl p-4 mb-6">
            Could not load events. Please try again.
            <button onClick={() => void mutate()} className="ml-3 underline font-bold">Try again</button>
          </div>
        )}

        {!error && (isLoading && !data ? (
          <EventGridSkeleton count={8} />
        ) : items.length > 0 ? (
          <>
            {/* Extended grid layout mapping on extra large screens: up to 5 cols! */}
            <div
              className={`grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6 transition-opacity duration-300 ${isValidating ? "opacity-60" : "opacity-100"}`}
            >
              {items.map((event) => (
                <EventCard key={event.id} event={event} />
              ))}
            </div>

            {totalPages > 1 && (
              <div className="mt-14 flex flex-wrap items-center justify-center gap-4">
                {prevHref ? (
                  <Link
                    href={prevHref}
                    scroll={false}
                    className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-5 py-2.5 text-sm font-bold text-foreground hover:border-primary hover:text-primary hover:shadow-md transition-all"
                  >
                    <ChevronLeft className="h-4 w-4" /> Previous
                  </Link>
                ) : (
                  <span className="inline-flex items-center gap-2 rounded-xl border border-border bg-card/50 px-5 py-2.5 text-sm font-bold text-muted-foreground opacity-50 cursor-not-allowed">
                    <ChevronLeft className="h-4 w-4" /> Previous
                  </span>
                )}
                <span className="text-sm font-bold text-muted-foreground px-2">
                  Page {page} of {totalPages}
                </span>
                {nextHref ? (
                  <Link
                    href={nextHref}
                    scroll={false}
                    className="inline-flex items-center gap-2 rounded-xl border border-border bg-card px-5 py-2.5 text-sm font-bold text-foreground hover:border-primary hover:text-primary hover:shadow-md transition-all"
                  >
                    Next <ChevronRight className="h-4 w-4" />
                  </Link>
                ) : (
                  <span className="inline-flex items-center gap-2 rounded-xl border border-border bg-card/50 px-5 py-2.5 text-sm font-bold text-muted-foreground opacity-50 cursor-not-allowed">
                    Next <ChevronRight className="h-4 w-4" />
                  </span>
                )}
              </div>
            )}
        </>
      ) : (
        <div className="rounded-2xl border border-dashed border-border py-28 text-center flex flex-col items-center bg-card shadow-sm mt-8 mx-auto max-w-2xl">
          <Search className="w-16 h-16 text-muted mb-6" />
          <h3 className="text-2xl font-extrabold mb-3">{hasActiveFilters ? "No matching events" : view.empty}</h3>
          <p className="text-muted-foreground mb-10 max-w-md mx-auto">
            {hasActiveFilters ? "Try broadening your search or changing your filters." : status === "past" ? "Completed events from the last 30 days will appear here." : "Check back soon for more community events."}
          </p>
          {hasActiveFilters && <button
            onClick={clearFilters}
            className="rounded-full bg-secondary text-foreground text-sm border border-border px-8 py-3 font-bold hover:bg-muted transition-all"
          >
            Reset Filters
          </button>}
        </div>
      ))}
    </div>
  </div>
  );
}
