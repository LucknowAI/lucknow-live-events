"use client";

import { Search, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useTransition } from "react";

export function FilterSidebar({ topics, localities }: { topics: any[], localities: any[] }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isPending, startTransition] = useTransition();

  const handleParamChange = useCallback(
    (name: string, value: string | null) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value === null || value === "") {
        params.delete(name);
      } else {
        params.set(name, value);
      }
      
      // Reset to page 1 on filter change
      if (name !== 'page') {
          params.delete('page');
      }

      startTransition(() => {
        router.push(`/events?${params.toString()}`, { scroll: false });
      });
    },
    [router, searchParams]
  );

  const q = searchParams.get("q") || "";
  const currentTopic = searchParams.get("topic") || "";
  const currentLocality = searchParams.get("locality") || "";
  const isFree = searchParams.get("is_free") === "true";
  const isStudentFriendly = searchParams.get("is_student_friendly") === "true";

  const hasActiveFilters = !!(q || currentTopic || currentLocality || isFree || isStudentFriendly || searchParams.get("page"));

  return (
    <aside className="w-full md:w-64 flex-shrink-0 border-r border-border bg-background p-6">
      <div className="flex items-center gap-2 mb-6">
        <SlidersHorizontal className={`w-5 h-5 text-primary ${isPending ? 'animate-spin' : ''}`} />
        <h2 className="text-lg font-bold">Filters</h2>
      </div>

      <div className="space-y-6">
        <div className="space-y-2">
          <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Search
          </label>
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              value={q}
              onChange={(e) => handleParamChange("q", e.target.value)}
              placeholder="Keywords..."
              className="w-full rounded-md border border-border bg-input py-2 pl-9 pr-3 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Topic
          </label>
          <select
            value={currentTopic}
            onChange={(e) => handleParamChange("topic", e.target.value)}
            className="w-full rounded-md border border-border bg-input py-2 px-3 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">Any topic</option>
            {topics.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-2">
          <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Locality
          </label>
          <select
            value={currentLocality}
            onChange={(e) => handleParamChange("locality", e.target.value)}
            className="w-full rounded-md border border-border bg-input py-2 px-3 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          >
            <option value="">Any locality</option>
            {localities.map((l) => (
              <option key={l.name} value={l.name}>
                {l.name}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-3 pt-2">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={isFree}
              onChange={(e) => handleParamChange("is_free", e.target.checked ? "true" : null)}
              className="rounded border-border text-primary focus:ring-accent bg-input"
            />
            <span className="text-sm font-medium">Free Events Only</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={isStudentFriendly}
              onChange={(e) => handleParamChange("is_student_friendly", e.target.checked ? "true" : null)}
              className="rounded border-border text-primary focus:ring-accent bg-input"
            />
            <span className="text-sm font-medium">Student Friendly</span>
          </label>
        </div>

        {hasActiveFilters && (
          <Link
            href="/events"
            prefetch={true}
            className="block text-center text-xs text-muted-foreground mt-4 py-2 border border-border rounded-md hover:bg-secondary hover:text-foreground transition-colors"
          >
            Clear All Filters
          </Link>
        )}
      </div>
    </aside>
  );
}
