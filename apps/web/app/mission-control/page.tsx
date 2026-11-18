"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { adminService, type StatsOut } from "@/lib/admin-api";
import {
  Shield,
  Loader2,
  LayoutDashboard,
  Database,
  Inbox,
  LogOut,
} from "lucide-react";

// Sub-components (loaded lazily per tab)
import { StatsPanel } from "./_components/StatsPanel";
import { EventsTab } from "./_components/EventsTab";
import { SourcesTab } from "./_components/SourcesTab";
import { ModerationTab } from "./_components/ModerationTab";

type Tab = "events" | "sources" | "moderation";

export default function MissionControlPage() {
  const router = useRouter();

  const [authed, setAuthed] = useState<boolean | null>(null);
  const [stats, setStats] = useState<StatsOut | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("events");

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      const token = localStorage.getItem("admin_token");
      if (!token) {
        router.replace("/mission-control/login");
        return;
      }
      adminService.stats().then((value) => {
        if (active) {
          setStats(value);
          setAuthed(true);
        }
      }).catch(() => {
        if (!active) return;
        localStorage.removeItem("admin_token");
        router.replace("/mission-control/login");
      });
    });
    return () => { active = false; };
  }, [router]);

  function logout() {
    localStorage.removeItem("admin_token");
    router.replace("/mission-control/login");
  }

  if (authed === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  const tabs: { id: Tab; label: string; icon: React.ReactNode; badge?: number }[] = [
    { id: "events", label: "Events", icon: <LayoutDashboard className="w-4 h-4" />, badge: stats?.events_total },
    { id: "sources", label: "Sources", icon: <Database className="w-4 h-4" />, badge: stats?.sources_active },
    { id: "moderation", label: "Community", icon: <Inbox className="w-4 h-4" /> },
  ];

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Top bar */}
      <header className="sticky top-0 z-50 border-b border-border bg-card/80 backdrop-blur-xl">
        <div className="max-w-screen-xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Shield className="w-5 h-5 text-primary" />
            <span className="font-extrabold tracking-tight text-sm">
              Mission Control <span className="text-muted-foreground font-normal">/ Lucknow Tech Events</span>
            </span>
          </div>
          <button
            onClick={logout}
            className="flex items-center gap-2 text-xs text-muted-foreground hover:text-destructive transition-colors"
          >
            <LogOut className="w-4 h-4" /> Sign out
          </button>
        </div>
      </header>

      <div className="max-w-screen-xl mx-auto w-full px-6 py-8 flex-1">
        {/* Stats row */}
        <StatsPanel stats={stats} onRefresh={() => adminService.stats().then(setStats)} />

        {/* Tab bar */}
        <div className="flex gap-1 mt-8 mb-6 border-b border-border overflow-x-auto">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-semibold whitespace-nowrap transition-colors motion-reduce:transition-none border-b-2 -mb-px ${
                activeTab === t.id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {t.icon}
              {t.label}
              {t.badge !== undefined && t.badge > 0 && (
                <span className={`text-[10px] font-extrabold px-1.5 py-0.5 rounded-full ${
                  t.id === "moderation" ? "bg-destructive/20 text-destructive" : "bg-primary/10 text-primary"
                }`}>
                  {t.badge}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {activeTab === "events" && <EventsTab />}
        {activeTab === "sources" && <SourcesTab />}
        {activeTab === "moderation" && <ModerationTab />}
      </div>
    </div>
  );
}
