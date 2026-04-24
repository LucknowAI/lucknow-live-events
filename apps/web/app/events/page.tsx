import { EventGridSkeleton } from "@/components/EventGridSkeleton";
import { EventsExplorer } from "./events-explorer";
import { Suspense } from "react";

export default function EventsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col min-h-full bg-muted/10">
          <div className="h-24 w-full bg-background border-b border-border shadow-sm flex items-center px-6 lg:px-10">
            <div className="flex gap-4 w-full max-w-screen-2xl mx-auto">
              <div className="h-10 flex-1 bg-muted rounded-full animate-pulse max-w-md" />
              <div className="h-10 w-32 bg-muted rounded-full animate-pulse" />
              <div className="h-10 w-40 bg-muted rounded-full animate-pulse hidden sm:block" />
              <div className="h-10 w-36 bg-muted rounded-full animate-pulse hidden lg:block" />
            </div>
          </div>
          <div className="flex-1 p-6 lg:p-10 w-full max-w-screen-2xl mx-auto">
            <div className="flex justify-between mt-4 mb-2">
              <div className="flex gap-4">
                 <div className="h-4 w-24 bg-muted rounded animate-pulse" />
                 <div className="h-4 w-32 bg-muted rounded animate-pulse" />
              </div>
              <div className="h-4 w-20 bg-muted rounded animate-pulse" />
            </div>
            <EventGridSkeleton count={8} />
          </div>
        </div>
      }
    >
      <EventsExplorer />
    </Suspense>
  );
}
