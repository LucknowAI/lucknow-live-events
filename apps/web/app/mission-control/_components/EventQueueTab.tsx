"use client";

import { useEffect, useState } from "react";
import { adminService, type QueueItem, type LastPublishedEvent } from "@/lib/admin-api";
import {
  Loader2, RefreshCw, Trash2, ExternalLink, CheckCircle2,
  Clock, AlertCircle, Zap
} from "lucide-react";

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  pending: { label: "Pending", color: "text-yellow-600 bg-yellow-500/10 border-yellow-500/20" },
  normalized: { label: "Normalizing", color: "text-blue-600 bg-blue-500/10 border-blue-500/20" },
  moderation: { label: "Flagged", color: "text-orange-600 bg-orange-500/10 border-orange-500/20" },
};

const REASON_LABELS: Record<string, string> = {
  low_confidence: "Low confidence",
  missing_start_at: "No date found",
  url_listing: "Listing page",
  url_noise: "Noise page",
};

export function EventQueueTab() {
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [lastPublished, setLastPublished] = useState<LastPublishedEvent | null>(null);
  const [loading, setLoading] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    const [q, lp] = await Promise.all([
      adminService.listEventQueue().catch(() => []),
      adminService.getLastPublished().catch(() => null),
    ]);
    setQueue(q);
    setLastPublished(lp);
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleRemove(id: string) {
    if (!confirm("Remove this item from the pipeline queue? This cannot be undone.")) return;
    setRemovingId(id);
    await adminService.removeFromQueue(id).catch(() => null);
    setQueue((q) => q.filter((item) => item.id !== id));
    setRemovingId(null);
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="font-extrabold text-lg">Event Pipeline Queue</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Raw events currently being processed by the AI pipeline. Fully automated — only remove if needed.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-primary transition-colors disabled:opacity-50"
        >
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          Refresh
        </button>
      </div>

      {/* Last Published Event */}
      {lastPublished && (
        <div className="mb-6 rounded-xl border border-green-500/20 bg-green-500/5 p-4">
          <div className="flex items-center gap-2 mb-2">
            <CheckCircle2 className="w-4 h-4 text-green-600" />
            <span className="text-xs font-extrabold text-green-700 uppercase tracking-wide">
              Last Published
            </span>
          </div>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="font-bold text-sm truncate">{lastPublished.title}</p>
              <div className="flex items-center gap-3 mt-1 flex-wrap">
                {lastPublished.date_tba ? (
                  <span className="text-xs text-yellow-600 font-semibold">📅 Date TBA</span>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    {new Date(lastPublished.start_at).toLocaleDateString("en-IN", {
                      day: "numeric", month: "short", year: "numeric",
                    })}
                  </span>
                )}
                {lastPublished.community_name && (
                  <span className="text-xs text-muted-foreground">
                    by {lastPublished.community_name}
                  </span>
                )}
                <span className="text-[11px] text-muted-foreground">
                  published {new Date(lastPublished.published_at).toLocaleTimeString("en-IN", {
                    hour: "2-digit", minute: "2-digit",
                  })}{" "}
                  {new Date(lastPublished.published_at).toLocaleDateString("en-IN", {
                    day: "numeric", month: "short",
                  })}
                </span>
              </div>
            </div>
            <a
              href={lastPublished.canonical_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-shrink-0 p-1.5 rounded-lg hover:bg-green-500/10 text-green-600"
            >
              <ExternalLink className="w-4 h-4" />
            </a>
          </div>
        </div>
      )}

      {loading && queue.length === 0 ? (
        <div className="flex items-center gap-2 text-muted-foreground py-12">
          <Loader2 className="w-5 h-5 animate-spin" /> Loading queue…
        </div>
      ) : queue.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <Zap className="w-12 h-12 text-muted-foreground/30 mb-3" />
          <p className="font-extrabold text-base">Queue is empty</p>
          <p className="text-sm text-muted-foreground mt-1">
            All events have been processed. Run discovery to find new ones.
          </p>
        </div>
      ) : (
        <div className="space-y-2 max-w-2xl">
          {queue.map((item) => {
            const isRemoving = removingId === item.id;
            const statusCfg = STATUS_CONFIG[item.pipeline_status] ?? STATUS_CONFIG.pending;
            return (
              <div
                key={item.id}
                className="rounded-xl border border-border bg-card px-4 py-3 flex items-center gap-3"
              >
                {/* Status badge */}
                <span
                  className={`inline-flex items-center gap-1 text-[10px] font-extrabold px-2 py-0.5 rounded-full border uppercase flex-shrink-0 ${statusCfg.color}`}
                >
                  {item.pipeline_status === "pending" && <Clock className="w-2.5 h-2.5" />}
                  {item.pipeline_status === "moderation" && <AlertCircle className="w-2.5 h-2.5" />}
                  {statusCfg.label}
                  {item.reason && ` · ${REASON_LABELS[item.reason] ?? item.reason}`}
                </span>

                {/* Title + meta */}
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold truncate">{item.title}</p>
                  <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                    {item.community && (
                      <span className="text-[11px] text-muted-foreground">{item.community}</span>
                    )}
                    <span className="text-[11px] text-muted-foreground">
                      confidence: {Math.round(item.extraction_confidence * 100)}%
                    </span>
                    {item.url && (
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[11px] text-primary hover:underline inline-flex items-center gap-0.5"
                      >
                        <ExternalLink className="w-2.5 h-2.5" />
                        source
                      </a>
                    )}
                  </div>
                </div>

                {/* Remove button */}
                <button
                  onClick={() => handleRemove(item.id)}
                  disabled={isRemoving}
                  title="Remove from queue"
                  className="flex-shrink-0 p-1.5 rounded-lg text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-40"
                >
                  {isRemoving ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Trash2 className="w-4 h-4" />
                  )}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
