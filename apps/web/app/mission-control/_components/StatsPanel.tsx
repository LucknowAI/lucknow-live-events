"use client";

import { type StatsOut } from "@/lib/admin-api";
import { Calendar, Database, Inbox, BarChart3, RefreshCw } from "lucide-react";
import { useState } from "react";

interface Props {
  stats: StatsOut | null;
  onRefresh: () => Promise<void>;
}

export function StatsPanel({ stats, onRefresh }: Props) {
  const [refreshing, setRefreshing] = useState(false);

  async function handleRefresh() {
    setRefreshing(true);
    await onRefresh().catch(() => null);
    setRefreshing(false);
  }

  const cards = [
    {
      label: "Total Events",
      value: stats?.events_total ?? "—",
      icon: <BarChart3 className="w-5 h-5" />,
      color: "text-blue-500 bg-blue-500/10",
    },
    {
      label: "Events This Week",
      value: stats?.events_this_week ?? "—",
      icon: <Calendar className="w-5 h-5" />,
      color: "text-green-500 bg-green-500/10",
    },
    {
      label: "Active Sources",
      value: stats?.sources_active ?? "—",
      icon: <Database className="w-5 h-5" />,
      color: "text-primary bg-primary/10",
    },
    {
      label: "Blacklisted Sources",
      value: stats?.sources_blacklisted ?? "—",
      icon: <Database className="w-5 h-5" />,
      color: "text-orange-500 bg-orange-500/10",
    },
    {
      label: "Pending Moderation",
      value: stats?.pending_moderation ?? "—",
      icon: <Inbox className="w-5 h-5" />,
      color: "text-destructive bg-destructive/10",
    },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-extrabold">Overview</h2>
        <button
          onClick={handleRefresh}
          className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-primary transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
        {cards.map((c) => (
          <div
            key={c.label}
            className="rounded-xl border border-border bg-card p-4 flex flex-col gap-2"
          >
            <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${c.color}`}>
              {c.icon}
            </div>
            <div>
              <div className="text-2xl font-extrabold">{c.value}</div>
              <div className="text-xs text-muted-foreground font-semibold mt-0.5">{c.label}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
