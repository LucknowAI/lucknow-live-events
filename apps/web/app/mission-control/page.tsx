"use client";

import { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { adminService, type StatsOut } from "@/lib/admin-api";
import { Shield, Loader2, LayoutDashboard, Database, Search, Inbox, List, LogOut } from "lucide-react";

// Sub-components (loaded lazily per tab)
import { StatsPanel } from "./_components/StatsPanel";
import { EventsTab } from "./_components/EventsTab";
import { SourcesTab } from "./_components/SourcesTab";
import { DiscoveryTab } from "./_components/DiscoveryTab";
import { ModerationTab } from "./_components/ModerationTab";
import { EventQueueTab } from "./_components/EventQueueTab";

const SECRET_KEY = "wakandaforever";
type Tab = "events" | "sources" | "discovery" | "moderation" | "queue";

function DashboardInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const key = searchParams.get("key");

  const [authed, setAuthed] = useState<boolean | null>(null);
  const [stats, setStats] = useState<StatsOut | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("events");

  // Gate: wrong key → fake 404
  if (key !== SECRET_KEY) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-background">
        <h1 className="text-6xl font-extrabold text-muted-foreground/20">404</h1>
        <p className="text-muted-foreground mt-3 text-sm">Page not found.</p>
      </div>
    );
  }

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (!token) {
      router.replace(`/mission-control/login?key=${SECRET_KEY}`);
      return;
    }
    setAuthed(true);
    adminService.stats().then(setStats).catch(() => {
      // Token expired
      localStorage.removeItem("admin_token");
      router.replace(`/mission-control/login?key=${SECRET_KEY}`);
    });
  }, []);

  function logout() {
    localStorage.removeItem("admin_token");
    router.replace(`/mission-control/login?key=${SECRET_KEY}`);
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
    { id: "discovery", label: "Discovery", icon: <Search className="w-4 h-4" /> },
    { id: "queue", label: "Queue", icon: <List className="w-4 h-4" /> },
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
        <div className="flex gap-1 mt-8 mb-6 border-b border-border">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-semibold transition-colors border-b-2 -mb-px ${
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
        {activeTab === "discovery" && <DiscoveryTab />}
        {activeTab === "queue" && <EventQueueTab />}
        {activeTab === "moderation" && <ModerationTab />}
      </div>
    </div>
  );
}

export default function MissionControlPage() {
  return (
    <Suspense>
      <DashboardInner />
    </Suspense>
  );
}
