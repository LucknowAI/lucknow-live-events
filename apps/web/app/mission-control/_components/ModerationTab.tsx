"use client";

import { useEffect, useState } from "react";
import { adminService, type CommunitySubmission } from "@/lib/admin-api";
import { Loader2, Globe, CheckCircle, XCircle, RefreshCw, Users } from "lucide-react";

export function ModerationTab() {
  const [items, setItems] = useState<CommunitySubmission[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionId, setActionId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    const data = await adminService.listCommunitySubmissions("pending").catch(() => []);
    setItems(data);
    setLoading(false);
  }

  useEffect(() => { load(); }, []);

  async function handleApprove(id: string) {
    setActionId(id);
    await adminService.approveModeration(id).catch(() => null);
    setItems((list) => list.filter((i) => i.id !== id));
    setActionId(null);
  }

  async function handleReject(id: string) {
    setActionId(id);
    await adminService.rejectModeration(id).catch(() => null);
    setItems((list) => list.filter((i) => i.id !== id));
    setActionId(null);
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground py-12">
        <Loader2 className="w-5 h-5 animate-spin" /> Loading community submissions…
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="font-extrabold text-lg">Community Review</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            Community links submitted by users for listing on the site. Review and approve or reject.
          </p>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-primary transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" /> Refresh
        </button>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <Users className="w-12 h-12 text-muted-foreground/40 mb-3" />
          <p className="font-extrabold text-base">No pending submissions</p>
          <p className="text-sm text-muted-foreground mt-1">
            Community link submissions will appear here when users submit them.
          </p>
        </div>
      ) : (
        <div className="space-y-3 max-w-2xl">
          {items.map((item) => {
            const isActioning = actionId === item.id;
            return (
              <div
                key={item.id}
                className="rounded-xl border border-border bg-card p-5 flex flex-col gap-4"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    {/* Community name */}
                    <div className="flex items-center gap-2 mb-1">
                      <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                        <Users className="w-3.5 h-3.5 text-primary" />
                      </div>
                      <p className="font-extrabold text-sm">{item.community_name ?? "Unknown Community"}</p>
                    </div>

                    {/* URL */}
                    {item.community_url && (
                      <a
                        href={item.community_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 text-xs text-primary hover:underline ml-9"
                      >
                        <Globe className="w-3 h-3 flex-shrink-0" />
                        <span className="truncate">{item.community_url}</span>
                      </a>
                    )}

                    {/* Description */}
                    {item.community_description && (
                      <p className="mt-2 text-xs text-muted-foreground leading-relaxed ml-9">
                        {item.community_description}
                      </p>
                    )}

                    {/* Submitted by */}
                    {item.submitter_name && (
                      <p className="mt-2 text-[11px] text-muted-foreground ml-9">
                        Submitted by <span className="font-semibold text-foreground">{item.submitter_name}</span>
                        {item.submitter_email && (
                          <span className="text-muted-foreground"> · {item.submitter_email}</span>
                        )}
                      </p>
                    )}

                    {/* Notes */}
                    {item.notes && (
                      <p className="mt-1.5 ml-9 text-xs italic text-muted-foreground">
                        &quot;{item.notes}&quot;
                      </p>
                    )}
                  </div>

                  {/* Date */}
                  <p className="text-[11px] text-muted-foreground whitespace-nowrap flex-shrink-0">
                    {new Date(item.created_at).toLocaleDateString("en-IN", {
                      day: "numeric",
                      month: "short",
                    })}
                  </p>
                </div>

                {/* Actions */}
                <div className="flex gap-2 border-t border-border pt-3">
                  <button
                    onClick={() => handleApprove(item.id)}
                    disabled={isActioning}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-green-500/10 text-green-600 text-xs font-bold border border-green-500/20 hover:bg-green-500/20 transition-colors disabled:opacity-50"
                  >
                    {isActioning ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <CheckCircle className="w-3.5 h-3.5" />
                    )}
                    Approve & List
                  </button>
                  <button
                    onClick={() => handleReject(item.id)}
                    disabled={isActioning}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-red-500/10 text-destructive text-xs font-bold border border-destructive/20 hover:bg-red-500/20 transition-colors disabled:opacity-50"
                  >
                    {isActioning ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <XCircle className="w-3.5 h-3.5" />
                    )}
                    Reject
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
