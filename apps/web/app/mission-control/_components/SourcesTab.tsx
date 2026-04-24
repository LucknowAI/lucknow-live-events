"use client";

import { useEffect, useState } from "react";
import { adminService, type AdminSource, type CrawlRun } from "@/lib/admin-api";
import { Loader2, Play, PlayCircle, Plus, Trash2, Shield, ShieldOff, ShieldCheck, X, History } from "lucide-react";

const STATUS_CONFIG = {
  active: { label: "Active", color: "bg-green-500/10 text-green-600" },
  whitelisted: { label: "Whitelisted", color: "bg-blue-500/10 text-blue-600" },
  blacklisted: { label: "Blacklisted", color: "bg-destructive/10 text-destructive" },
};

function AddSourceModal({ onClose, onAdded }: { onClose: () => void; onAdded: (s: AdminSource) => void }) {
  const [form, setForm] = useState({ name: "", base_url: "", platform: "", crawl_interval_hours: 6 });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const s = await adminService.createSource({ name: form.name, base_url: form.base_url, platform: form.platform || undefined });
      onAdded(s);
      onClose();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Failed to add source");
    } finally { setSaving(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-card border border-border rounded-2xl w-full max-w-md shadow-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-extrabold text-lg">Add Source</h3>
          <button onClick={onClose}><X className="w-5 h-5" /></button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          {[
            { label: "Name", key: "name", placeholder: "GDG Lucknow Meetup", required: true },
            { label: "Base URL", key: "base_url", placeholder: "https://lu.ma/gdg-lucknow", required: true },
            { label: "Platform (optional)", key: "platform", placeholder: "luma / commudle / unstop", required: false },
          ].map(({ label, key, placeholder, required }) => (
            <div key={key}>
              <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{label}</label>
              <input
                required={required}
                placeholder={placeholder}
                value={(form as any)[key]}
                onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                className="mt-1 w-full rounded-lg border border-border bg-input px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />
            </div>
          ))}
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex gap-3 pt-1">
            <button type="button" onClick={onClose} className="flex-1 rounded-lg border border-border py-2 text-sm font-semibold">Cancel</button>
            <button type="submit" disabled={saving} className="flex-1 rounded-lg bg-primary text-primary-foreground py-2 text-sm font-bold disabled:opacity-50">
              {saving ? <Loader2 className="w-4 h-4 animate-spin mx-auto" /> : "Add Source"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function SourcesTab() {
  const [sources, setSources] = useState<AdminSource[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionId, setActionId] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [taskMsg, setTaskMsg] = useState("");
  const [crawlHistory, setCrawlHistory] = useState<CrawlRun[]>([]);

  async function load() {
    setLoading(true);
    const [data, runs] = await Promise.all([
      adminService.listSources().catch(() => [] as AdminSource[]),
      adminService.crawlRuns().catch(() => [] as CrawlRun[]),
    ]);
    setSources(data);
    setCrawlHistory(runs.slice(0, 15));
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function setStatus(id: string, status: "active" | "whitelisted" | "blacklisted") {
    setActionId(id);
    const updated = await adminService.setSourceStatus(id, status).catch(() => null);
    if (updated) setSources((list) => list.map((s) => s.id === id ? updated : s));
    setActionId(null);
  }

  async function crawl(id: string) {
    setActionId(id);
    const r = await adminService.triggerCrawl(id).catch(() => null);
    if (r) setTaskMsg(`Crawl queued: task ${r.task_id.slice(0, 8)}…`);
    setActionId(null);
  }

  async function crawlAll() {
    const r = await adminService.triggerAll().catch(() => null);
    if (r) setTaskMsg(`Full crawl queued: task ${r.task_id.slice(0, 8)}…`);
  }

  async function deleteSource(id: string) {
    if (!confirm("Delete this source permanently?")) return;
    setActionId(id);
    await adminService.deleteSource(id).catch(() => null);
    setSources((list) => list.filter((s) => s.id !== id));
    setActionId(null);
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-extrabold text-base">{sources.length} Sources</h2>
        <div className="flex gap-2">
          {taskMsg && <span className="text-xs text-primary font-semibold">{taskMsg}</span>}
          <button onClick={crawlAll} className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-xs font-bold hover:bg-muted transition-colors">
            <PlayCircle className="w-4 h-4" /> Crawl All
          </button>
          <button onClick={() => setShowAdd(true)} className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-bold">
            <Plus className="w-4 h-4" /> Add Source
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex justify-center py-16"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : (
        <div className="rounded-xl border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30">
                <th className="text-left px-4 py-3 font-semibold text-muted-foreground text-xs uppercase">Source</th>
                <th className="text-left px-4 py-3 font-semibold text-muted-foreground text-xs uppercase hidden md:table-cell">Platform</th>
                <th className="text-left px-4 py-3 font-semibold text-muted-foreground text-xs uppercase">Status</th>
                <th className="text-left px-4 py-3 font-semibold text-muted-foreground text-xs uppercase hidden lg:table-cell">Trust</th>
                <th className="text-left px-4 py-3 font-semibold text-muted-foreground text-xs uppercase hidden lg:table-cell">Last Crawled</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sources.map((src) => {
                const cfg = STATUS_CONFIG[src.status] ?? STATUS_CONFIG.active;
                return (
                  <tr key={src.id} className="hover:bg-muted/20 transition-colors group">
                    <td className="px-4 py-3">
                      <div className="font-semibold">{src.name}</div>
                      <div className="text-xs text-muted-foreground truncate max-w-[200px]">{src.base_url}</div>
                    </td>
                    <td className="px-4 py-3 hidden md:table-cell text-muted-foreground capitalize">{src.platform ?? "—"}</td>
                    <td className="px-4 py-3">
                      <span className={`text-[10px] font-extrabold px-2 py-0.5 rounded-full uppercase ${cfg.color}`}>
                        {cfg.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 hidden lg:table-cell">
                      <div className="flex items-center gap-1.5">
                        <div className="h-1.5 w-16 bg-muted rounded-full overflow-hidden">
                          <div className="h-full bg-primary rounded-full" style={{ width: `${src.trust_score * 100}%` }} />
                        </div>
                        <span className="text-xs text-muted-foreground">{Math.round(src.trust_score * 100)}%</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 hidden lg:table-cell text-xs text-muted-foreground">
                      {src.last_crawled_at ? new Date(src.last_crawled_at).toLocaleDateString("en-IN") : "Never"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                        <button onClick={() => crawl(src.id)} disabled={actionId === src.id} title="Crawl now" className="p-1.5 rounded-lg hover:bg-muted">
                          <Play className="w-3.5 h-3.5" />
                        </button>
                        {src.status !== "whitelisted" && (
                          <button onClick={() => setStatus(src.id, "whitelisted")} disabled={actionId === src.id} title="Whitelist" className="p-1.5 rounded-lg hover:bg-blue-500/10">
                            <ShieldCheck className="w-3.5 h-3.5 text-blue-500" />
                          </button>
                        )}
                        {src.status !== "blacklisted" && (
                          <button onClick={() => setStatus(src.id, "blacklisted")} disabled={actionId === src.id} title="Blacklist" className="p-1.5 rounded-lg hover:bg-destructive/10">
                            <ShieldOff className="w-3.5 h-3.5 text-destructive" />
                          </button>
                        )}
                        {src.status !== "active" && (
                          <button onClick={() => setStatus(src.id, "active")} disabled={actionId === src.id} title="Set Active" className="p-1.5 rounded-lg hover:bg-green-500/10">
                            <Shield className="w-3.5 h-3.5 text-green-500" />
                          </button>
                        )}
                        <button onClick={() => deleteSource(src.id)} disabled={actionId === src.id} title="Delete" className="p-1.5 rounded-lg hover:bg-destructive/10 text-destructive">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {showAdd && <AddSourceModal onClose={() => setShowAdd(false)} onAdded={(s) => { setSources((list) => [s, ...list]); setShowAdd(false); }} />}

      {/* Crawl Run History */}
      {crawlHistory.length > 0 && (
        <div className="mt-8">
          <div className="flex items-center gap-2 mb-3">
            <History className="w-4 h-4 text-muted-foreground" />
            <h3 className="font-extrabold text-sm">Recent Crawl Runs</h3>
          </div>
          <div className="rounded-xl border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/30">
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-muted-foreground uppercase">Status</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-muted-foreground uppercase hidden md:table-cell">Started</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-muted-foreground uppercase">Found</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-muted-foreground uppercase hidden lg:table-cell">New</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-muted-foreground uppercase hidden lg:table-cell">Published</th>
                  <th className="text-left px-4 py-2.5 text-xs font-semibold text-muted-foreground uppercase hidden xl:table-cell">Error</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {crawlHistory.map((run) => {
                  const statusColor =
                    run.status === "success" ? "bg-green-500/10 text-green-600" :
                    run.status === "error" ? "bg-destructive/10 text-destructive" :
                    run.status === "running" ? "bg-blue-500/10 text-blue-600" :
                    "bg-muted text-muted-foreground";

                  const durationMs = run.finished_at
                    ? new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()
                    : null;
                  const duration = durationMs != null
                    ? durationMs > 60000 ? `${Math.round(durationMs / 60000)}m` : `${Math.round(durationMs / 1000)}s`
                    : "—";

                  return (
                    <tr key={run.id} className="hover:bg-muted/10 transition-colors">
                      <td className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <span className={`text-[10px] font-extrabold px-2 py-0.5 rounded-full uppercase ${statusColor}`}>
                            {run.status ?? "unknown"}
                          </span>
                          <span className="text-xs text-muted-foreground hidden sm:inline">{duration}</span>
                        </div>
                      </td>
                      <td className="px-4 py-2.5 hidden md:table-cell text-xs text-muted-foreground">
                        {new Date(run.started_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                      </td>
                      <td className="px-4 py-2.5 font-semibold text-sm">{run.events_found}</td>
                      <td className="px-4 py-2.5 hidden lg:table-cell text-green-600 font-semibold text-sm">+{run.events_new}</td>
                      <td className="px-4 py-2.5 hidden lg:table-cell text-primary font-semibold text-sm">{run.events_published}</td>
                      <td className="px-4 py-2.5 hidden xl:table-cell text-xs text-muted-foreground max-w-[180px] truncate">
                        {run.error_summary ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
