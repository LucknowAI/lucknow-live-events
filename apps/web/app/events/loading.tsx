import { Search, SlidersHorizontal } from "lucide-react";

export default function EventsLoading() {
  return (
    <div className="flex flex-col md:flex-row min-h-full">
      <aside className="w-full md:w-64 flex-shrink-0 border-r border-border bg-background p-6 opacity-70">
        <div className="flex items-center gap-2 mb-6">
          <SlidersHorizontal className="w-5 h-5 text-primary" />
          <h2 className="text-lg font-bold">Filters</h2>
        </div>
        <div className="space-y-6 animate-pulse">
          <div className="h-10 bg-muted rounded-md w-full" />
          <div className="h-10 bg-muted rounded-md w-full" />
          <div className="h-10 bg-muted rounded-md w-full" />
          <div className="h-4 bg-muted rounded w-1/2" />
          <div className="h-4 bg-muted rounded w-1/2" />
          <div className="h-10 bg-muted rounded-md w-full mt-4" />
        </div>
      </aside>

      <div className="flex-1 p-6 lg:p-10">
        <div className="flex items-center justify-between mb-8 flex-wrap gap-4">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">All Events</h1>
          <div className="h-8 w-24 bg-muted rounded-full animate-pulse" />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="flex flex-col rounded-2xl border border-border bg-card overflow-hidden h-full animate-pulse">
              <div className="h-40 bg-muted/50" />
              <div className="p-5 flex-1 flex flex-col space-y-4">
                <div className="flex gap-2">
                  <div className="h-6 w-16 bg-muted rounded-full" />
                  <div className="h-6 w-16 bg-muted rounded-full" />
                </div>
                <div className="h-6 w-3/4 bg-muted rounded" />
                <div className="h-4 w-1/2 bg-muted rounded" />
                <div className="h-4 w-full bg-muted rounded mt-auto" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
