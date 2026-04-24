"use client";

import { adminService, type AdminSource, type CommunitySubmission, type StatsOut } from "@/lib/admin-api";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

export default function AdminDashboardPage() {
  const router = useRouter();
  const [sources, setSources] = useState<AdminSource[]>([]);
  const [queue, setQueue] = useState<CommunitySubmission[]>([]);
  const [stats, setStats] = useState<StatsOut | null>(null);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, q, st] = await Promise.all([
        adminService.listSources(),
        adminService.listCommunitySubmissions(),
        adminService.stats(),
      ]);
      setErr("");
      setSources(s);
      setQueue(q);
      setStats(st);
    } catch {
      setErr("Unauthorized or API unreachable. Sign in again.");
      localStorage.removeItem("admin_token");
      router.replace("/admin/login");
    }
  }, [router]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!localStorage.getItem("admin_token")) {
      router.replace("/admin/login");
      return;
    }
    // Defer fetch so this effect does not synchronously invoke a function that updates React state.
    queueMicrotask(() => {
      void load();
    });
  }, [load, router]);

  function logout() {
    localStorage.removeItem("admin_token");
    router.push("/admin/login");
  }

  async function toggleSource(id: string, enabled: boolean) {
    setMsg("");
    try {
      const updated = await adminService.patchSource(id, { enabled: !enabled });
      setSources((prev) => prev.map((s) => (s.id === id ? updated : s)));
      setMsg("Source updated.");
    } catch {
      setErr("Failed to update source.");
    }
  }

  async function crawlOne(id: string) {
    setMsg("");
    try {
      const r = await adminService.triggerCrawl(id);
      setMsg(`Crawl queued: ${r.task_id}`);
    } catch {
      setErr("Failed to queue crawl.");
    }
  }

  async function crawlAll() {
    setMsg("");
    try {
      const r = await adminService.triggerAll();
      setMsg(`Crawl-all queued: ${r.task_id}`);
    } catch {
      setErr("Failed to queue crawl-all.");
    }
  }

  async function approve(id: string) {
    setMsg("");
    try {
      await adminService.approveModeration(id);
      setQueue((q) => q.filter((x) => x.id !== id));
      void load();
    } catch {
      setErr("Approve failed.");
    }
  }

  async function reject(id: string) {
    setMsg("");
    try {
      await adminService.rejectModeration(id);
      setQueue((q) => q.filter((x) => x.id !== id));
      void load();
    } catch {
      setErr("Reject failed.");
    }
  }

  return (
    <div className="max-w-5xl mx-auto py-10 px-6 space-y-10">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Admin</h1>
          <p className="text-sm text-muted-foreground">Sources, crawls, and moderation queue</p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => void load()} className="rounded-md border border-border px-3 py-1.5 text-sm">
            Refresh
          </button>
          <button type="button" onClick={logout} className="rounded-md border border-border px-3 py-1.5 text-sm">
            Log out
          </button>
          <Link href="/" className="rounded-md border border-border px-3 py-1.5 text-sm inline-flex items-center">
            Site
          </Link>
        </div>
      </div>

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat label="Events" value={stats.events_total} />
          <Stat label="This week" value={stats.events_this_week} />
          <Stat label="Moderation" value={stats.pending_moderation} />
          <Stat label="Active sources" value={stats.sources_active} />
        </div>
      )}

      {msg && <p className="text-sm text-primary">{msg}</p>}
      {err && <p className="text-sm text-destructive">{err}</p>}

      <section className="space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <h2 className="text-xl font-semibold">Sources</h2>
          <button
            type="button"
            onClick={() => void crawlAll()}
            className="rounded-md bg-secondary px-3 py-1.5 text-sm font-medium border border-border"
          >
            Crawl all sources
          </button>
        </div>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left">
              <tr>
                <th className="p-3">Name</th>
                <th className="p-3">Platform</th>
                <th className="p-3">Enabled</th>
                <th className="p-3">Last crawl</th>
                <th className="p-3"></th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id} className="border-t border-border">
                  <td className="p-3 font-medium">{s.name}</td>
                  <td className="p-3 text-muted-foreground">{s.platform}</td>
                  <td className="p-3">
                    <button
                      type="button"
                      onClick={() => void toggleSource(s.id, s.enabled)}
                      className={s.enabled ? "text-primary font-semibold" : "text-muted-foreground"}
                    >
                      {s.enabled ? "On" : "Off"}
                    </button>
                  </td>
                  <td className="p-3 text-xs text-muted-foreground">
                    {s.last_crawled_at ? new Date(s.last_crawled_at).toLocaleString() : "—"}
                  </td>
                  <td className="p-3">
                    <button
                      type="button"
                      onClick={() => void crawlOne(s.id)}
                      className="text-primary text-xs font-semibold hover:underline"
                    >
                      Crawl
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-xl font-semibold">Moderation queue</h2>
        {queue.length === 0 ? (
          <p className="text-muted-foreground text-sm">No pending items.</p>
        ) : (
          <ul className="space-y-2">
            {queue.map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border p-4"
              >
                <div>
                  <p className="font-mono text-xs text-muted-foreground">{item.id}</p>
                  <p className="text-sm font-medium">{item.community_name || "—"}</p>
                  <p className="text-xs text-muted-foreground">{item.notes || item.status}</p>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => void approve(item.id)}
                    className="rounded-md bg-primary text-primary-foreground px-3 py-1 text-sm"
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => void reject(item.id)}
                    className="rounded-md border border-border px-3 py-1 text-sm"
                  >
                    Reject
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-border p-4 bg-card">
      <p className="text-xs text-muted-foreground uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
    </div>
  );
}
