export function EventGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 animate-pulse">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col overflow-hidden rounded-xl border border-border bg-card h-[320px]"
        >
          <div className="h-40 w-full bg-muted" />
          <div className="p-5 space-y-3 flex-1">
            <div className="h-4 w-24 rounded bg-muted" />
            <div className="h-5 w-full rounded bg-muted" />
            <div className="h-5 w-4/5 rounded bg-muted" />
            <div className="h-3 w-full rounded bg-muted mt-4" />
            <div className="h-3 w-2/3 rounded bg-muted" />
            <div className="mt-auto pt-4 h-10 w-full rounded-lg bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}
