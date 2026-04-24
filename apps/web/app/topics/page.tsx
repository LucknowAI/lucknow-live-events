import { facetService } from "@/lib/api";
import { Hash } from "lucide-react";
import Link from "next/link";

export const metadata = {
  title: "Topics",
  description: "Browse events by technology topic in Lucknow.",
};

export default async function TopicsPage() {
  let items: Awaited<ReturnType<typeof facetService.getTopics>> = [];
  try {
    items = await facetService.getTopics();
  } catch {
    /* empty */
  }

  return (
    <div className="max-w-3xl mx-auto py-12 px-6">
      <div className="flex items-center gap-3 mb-8">
        <Hash className="w-8 h-8 text-primary" />
        <div>
          <h1 className="text-3xl font-bold">Topics</h1>
          <p className="text-muted-foreground text-sm">From published events on the platform.</p>
        </div>
      </div>
      {items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border p-12 text-center">
          <p className="text-muted-foreground mb-4">No topics yet — run a crawl or submit an event.</p>
          <Link href="/submit" className="text-primary font-semibold hover:underline">
            Submit an event
          </Link>
        </div>
      ) : (
        <ul className="space-y-2">
          {items.map((t) => (
            <li key={t.name}>
              <Link
                href={`/events?topic=${encodeURIComponent(t.name)}`}
                className="flex justify-between rounded-lg border border-border bg-card px-4 py-3 hover:border-primary transition-colors"
              >
                <span className="font-medium">{t.name}</span>
                <span className="text-muted-foreground text-sm">{t.count} events</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
