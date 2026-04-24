"use client";

import { useEffect, useState } from "react";
import { adminService, type AdminEvent, type EventUpdate } from "@/lib/admin-api";
import { Loader2, Search, Trash2, RefreshCw, Star, ExternalLink, Pencil, X, Clock } from "lucide-react";

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit", month: "short", year: "numeric",
  });
}

/**
 * Convert a UTC ISO string → datetime-local input value in IST (YYYY-MM-DDTHH:MM).
 * HTML datetime-local inputs have no timezone concept, so we pre-shift to IST
 * so what the admin sees matches what's printed on the event card.
 */
function toISTInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const utcMs = new Date(iso).getTime();
  const istMs = utcMs + (5 * 60 + 30) * 60 * 1000; // +05:30
  return new Date(istMs).toISOString().slice(0, 16);   // "YYYY-MM-DDTHH:MM"
}

/**
 * Convert a datetime-local input value (entered in IST) → ISO 8601 with +05:30.
 * The backend Pydantic parser handles the offset and stores UTC correctly.
 */
function fromISTInput(val: string): string | undefined {
  if (!val) return undefined;
  return `${val}:00+05:30`;
}

function EditModal({ event, onClose, onSaved }: {
  event: AdminEvent;
  onClose: () => void;
  onSaved: (updated: AdminEvent) => void;
}) {
  const [form, setForm] = useState<EventUpdate>({
    title: event.title,
    registration_url: event.registration_url ?? "",
    canonical_url: event.canonical_url,
    city: event.city ?? "",
    locality: event.locality ?? "",
    venue_name: event.venue_name ?? "",
    mode: event.mode ?? "",
    event_type: event.event_type ?? "",
    is_free: event.is_free,
    is_featured: event.is_featured,
    is_cancelled: event.is_cancelled,
  });

  // Separate IST string state for the two datetime-local inputs
  const [startIST, setStartIST] = useState(toISTInput(event.start_at));
  const [endIST, setEndIST] = useState(toISTInput(event.end_at));

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setSaving(true);
    setError("");
    try {
      const payload: EventUpdate = {
        ...form,
        ...(startIST ? { start_at: fromISTInput(startIST) } : {}),
        ...(endIST   ? { end_at:   fromISTInput(endIST)   } : {}),
      };
      const updated = await adminService.updateEvent(event.id, payload);
      onSaved(updated as unknown as AdminEvent);
      onClose();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-card border border-border rounded-2xl w-full max-w-xl shadow-2xl p-6 space-y-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between">
          <h3 className="font-extrabold text-lg">Edit Event</h3>
          <button onClick={onClose}><X className="w-5 h-5" /></button>
        </div>

        {/* ── Date & Time ────────────────────────────────────────── */}
        <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-3">
          <div className="flex items-center gap-2 text-xs font-bold text-muted-foreground uppercase tracking-wider">
            <Clock className="w-3.5 h-3.5" />
            Date &amp; Time <span className="font-normal normal-case">(IST — Asia/Kolkata)</span>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-semibold text-muted-foreground">Start</label>
              <input
                type="datetime-local"
                className="mt-1 w-full rounded-lg border border-border bg-input px-3 py-2 text-sm focus:border-primary focus:outline-none"
                value={startIST}
                onChange={(e) => setStartIST(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-muted-foreground">End <span className="font-normal">(optional)</span></label>
              <input
                type="datetime-local"
                className="mt-1 w-full rounded-lg border border-border bg-input px-3 py-2 text-sm focus:border-primary focus:outline-none"
                value={endIST}
                onChange={(e) => setEndIST(e.target.value)}
              />
            </div>
          </div>
        </div>

        {/* ── Text fields ────────────────────────────────────────── */}
        {[
          { label: "Title", key: "title" as const },
          { label: "Registration URL", key: "registration_url" as const },
          { label: "Canonical URL", key: "canonical_url" as const },
          { label: "City", key: "city" as const },
          { label: "Locality", key: "locality" as const },
          { label: "Venue", key: "venue_name" as const },
          { label: "Mode (offline/online/hybrid)", key: "mode" as const },
          { label: "Event Type", key: "event_type" as const },
        ].map(({ label, key }) => (
          <div key={key}>
            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">{label}</label>
            <input
              className="mt-1 w-full rounded-lg border border-border bg-input px-3 py-2 text-sm focus:border-primary focus:outline-none"
              value={(form[key] as string) ?? ""}
              onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
            />
          </div>
        ))}

        {/* ── Flags ─────────────────────────────────────────────── */}
        <div className="flex gap-6">
          {(["is_free", "is_featured", "is_cancelled"] as const).map((k) => (
            <label key={k} className="flex items-center gap-2 text-sm font-semibold cursor-pointer">
              <input
                type="checkbox"
                checked={!!form[k]}
                onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.checked }))}
                className="rounded border-border"
              />
              {k.replace("is_", "").replace("_", " ")}
            </label>
          ))}
        </div>

        {error && <p className="text-xs text-destructive">{error}</p>}
        <div className="flex gap-3 pt-2">
          <button onClick={onClose} className="flex-1 rounded-lg border border-border py-2 text-sm font-semibold">Cancel</button>
          <button onClick={save} disabled={saving} className="flex-1 rounded-lg bg-primary text-primary-foreground py-2 text-sm font-bold disabled:opacity-50">
            {saving ? <Loader2 className="w-4 h-4 animate-spin mx-auto" /> : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function EventsTab() {
  const [events, setEvents] = useState<AdminEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<AdminEvent | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);

  async function load(pg = 1, query = q) {
    setLoading(true);
    try {
      const res = await adminService.listEvents(pg, 30, query || undefined);
      setEvents(res.items);
      setTotal(res.total);
      setPage(pg);
    } catch { /* ignore */ }
    setLoading(false);
  }

  useEffect(() => { load(1); }, []);

  async function handleDelete(id: string) {
    if (!confirm("Permanently delete this event?")) return;
    setActionId(id);
    await adminService.deleteEvent(id).catch(() => null);
    setEvents((ev) => ev.filter((e) => e.id !== id));
    setActionId(null);
  }

  async function handleRescrape(id: string) {
    setActionId(id);
    await adminService.rescrapeEvent(id).catch(() => null);
    setActionId(null);
    alert("Re-scrape queued!");
  }

  async function handleFeature(ev: AdminEvent) {
    setActionId(ev.id);
    const updated = await adminService.featureEvent(ev.id, !ev.is_featured).catch(() => null);
    if (updated) setEvents((list) => list.map((e) => e.id === ev.id ? { ...e, is_featured: !e.is_featured } : e));
    setActionId(null);
  }

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            className="w-full pl-9 pr-4 py-2 rounded-lg border border-border bg-input text-sm focus:border-primary focus:outline-none"
            placeholder="Search events by title…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load(1, q)}
          />
        </div>
        <button onClick={() => load(1, q)} className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-bold">
          Search
        </button>
      </div>

      <div className="text-xs text-muted-foreground mb-3 font-semibold">{total} events total</div>

      {loading ? (
        <div className="flex justify-center py-16"><Loader2 className="w-8 h-8 animate-spin text-primary" /></div>
      ) : (
        <div className="rounded-xl border border-border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30">
                <th className="text-left px-4 py-3 font-semibold text-muted-foreground text-xs uppercase">Event</th>
                <th className="text-left px-4 py-3 font-semibold text-muted-foreground text-xs uppercase hidden md:table-cell">Date</th>
                <th className="text-left px-4 py-3 font-semibold text-muted-foreground text-xs uppercase hidden lg:table-cell">Type</th>
                <th className="text-left px-4 py-3 font-semibold text-muted-foreground text-xs uppercase hidden lg:table-cell">Status</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {events.map((ev) => (
                <tr key={ev.id} className="hover:bg-muted/20 transition-colors group">
                  <td className="px-4 py-3">
                    <div className="font-semibold line-clamp-1">{ev.title}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">{ev.community_name ?? "—"}</div>
                  </td>
                  <td className="px-4 py-3 hidden md:table-cell text-muted-foreground">{formatDate(ev.start_at)}</td>
                  <td className="px-4 py-3 hidden lg:table-cell">
                    <span className="bg-muted px-2 py-0.5 rounded-full text-xs capitalize">{ev.event_type ?? "—"}</span>
                  </td>
                  <td className="px-4 py-3 hidden lg:table-cell">
                    <div className="flex gap-1.5">
                      {ev.is_featured && <span className="bg-yellow-500/10 text-yellow-600 text-[10px] font-bold px-1.5 py-0.5 rounded-full">Featured</span>}
                      {ev.is_cancelled && <span className="bg-destructive/10 text-destructive text-[10px] font-bold px-1.5 py-0.5 rounded-full">Cancelled</span>}
                      {!ev.published_at && <span className="bg-muted text-muted-foreground text-[10px] font-bold px-1.5 py-0.5 rounded-full">Draft</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 justify-end opacity-0 group-hover:opacity-100 transition-opacity">
                      <a href={ev.canonical_url} target="_blank" rel="noopener noreferrer" className="p-1.5 rounded-lg hover:bg-muted" title="View source">
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                      <button onClick={() => setEditing(ev)} className="p-1.5 rounded-lg hover:bg-muted" title="Edit">
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handleFeature(ev)} disabled={actionId === ev.id} className="p-1.5 rounded-lg hover:bg-muted" title={ev.is_featured ? "Unfeature" : "Feature"}>
                        <Star className={`w-3.5 h-3.5 ${ev.is_featured ? "fill-yellow-400 text-yellow-400" : ""}`} />
                      </button>
                      <button onClick={() => handleRescrape(ev.id)} disabled={actionId === ev.id} className="p-1.5 rounded-lg hover:bg-muted" title="Re-scrape">
                        <RefreshCw className="w-3.5 h-3.5" />
                      </button>
                      <button onClick={() => handleDelete(ev.id)} disabled={actionId === ev.id} className="p-1.5 rounded-lg hover:bg-destructive/10 text-destructive" title="Delete">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      <div className="flex items-center justify-between mt-4">
        <button disabled={page <= 1} onClick={() => load(page - 1)} className="px-3 py-1.5 rounded-lg border border-border text-xs font-bold disabled:opacity-40">← Prev</button>
        <span className="text-xs text-muted-foreground">Page {page} · {total} total</span>
        <button disabled={events.length < 30} onClick={() => load(page + 1)} className="px-3 py-1.5 rounded-lg border border-border text-xs font-bold disabled:opacity-40">Next →</button>
      </div>

      {editing && (
        <EditModal
          event={editing}
          onClose={() => setEditing(null)}
          onSaved={(updated) => {
            setEvents((list) => list.map((e) => e.id === updated.id ? updated : e));
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}
