"use client";

import { useState } from "react";
import { adminService } from "@/lib/admin-api";
import { Loader2, Search, Send, Plus, X, Sparkles } from "lucide-react";

export function DiscoveryTab() {
  const [running, setRunning] = useState(false);
  const [taskMsg, setTaskMsg] = useState("");
  const [customQueries, setCustomQueries] = useState<string[]>([""]);
  const [submitUrl, setSubmitUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitMsg, setSubmitMsg] = useState("");

  async function runDefault() {
    setRunning(true);
    setTaskMsg("");
    try {
      const r = await adminService.runDiscovery();
      setTaskMsg(`✅ Discovery queued — task ${r.task_id.slice(0, 12)}…`);
    } catch (e: any) {
      setTaskMsg(`❌ ${e?.response?.data?.detail ?? e?.message ?? "Failed to queue discovery"}`);
    }
    setRunning(false);
  }

  async function runCustom() {
    const queries = customQueries.filter((q) => q.trim());
    if (!queries.length) return;
    setRunning(true);
    setTaskMsg("");
    try {
      const r = await adminService.runCustomDiscovery(queries);
      setTaskMsg(`✅ Custom discovery queued — task ${r.task_id.slice(0, 12)}…`);
    } catch (e: any) {
      setTaskMsg(`❌ ${e?.response?.data?.detail ?? e?.message ?? "Failed to queue custom discovery"}`);
    }
    setRunning(false);
  }

  async function handleSubmitUrl(e: React.FormEvent) {
    e.preventDefault();
    if (!submitUrl.trim()) return;
    setSubmitting(true);
    setSubmitMsg("");
    try {
      const r = await adminService.submitUrl(submitUrl.trim());
      setSubmitMsg(`✅ Queued — submission ${r.submission_id.slice(0, 8)}…`);
    } catch (e: any) {
      setSubmitMsg(`❌ ${e?.response?.data?.detail ?? e?.message ?? "Failed to submit URL"}`);
    }
    setSubmitting(false);
    setSubmitUrl("");
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Auto Discovery */}
      <div className="rounded-xl border border-border bg-card p-6">
        <div className="flex items-center gap-3 mb-2">
          <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
            <Sparkles className="w-5 h-5 text-primary" />
          </div>
          <div>
            <h3 className="font-extrabold text-base">Auto Discovery</h3>
            <p className="text-xs text-muted-foreground">
              Run the AI agent with default Lucknow-focused search queries (4-month window)
            </p>
          </div>
        </div>
        <button
          onClick={runDefault}
          disabled={running}
          className="mt-4 flex items-center gap-2 px-5 py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-bold disabled:opacity-50 hover:bg-primary/90 transition-colors"
        >
          {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          Run Discovery Now
        </button>
        {taskMsg && <p className="mt-3 text-xs font-semibold">{taskMsg}</p>}
      </div>

      {/* Custom Queries */}
      <div className="rounded-xl border border-border bg-card p-6">
        <h3 className="font-extrabold text-base mb-1">Custom Search Queries</h3>
        <p className="text-xs text-muted-foreground mb-4">
          Add your own search prompts. The AI will use Google Search with these queries.
        </p>
        <div className="space-y-2">
          {customQueries.map((q, i) => (
            <div key={i} className="flex gap-2">
              <input
                value={q}
                onChange={(e) =>
                  setCustomQueries((qs) => qs.map((v, j) => (j === i ? e.target.value : v)))
                }
                placeholder={`e.g. "GDG Lucknow events May 2026 site:lu.ma"`}
                className="flex-1 rounded-lg border border-border bg-input px-3 py-2 text-sm focus:border-primary focus:outline-none"
              />
              {customQueries.length > 1 && (
                <button
                  onClick={() => setCustomQueries((qs) => qs.filter((_, j) => j !== i))}
                  className="p-2 rounded-lg hover:bg-muted"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="flex gap-3 mt-3">
          <button
            onClick={() => setCustomQueries((qs) => [...qs, ""])}
            className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground hover:text-primary transition-colors"
          >
            <Plus className="w-3.5 h-3.5" /> Add Query
          </button>
          <button
            onClick={runCustom}
            disabled={running}
            className="ml-auto flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-bold disabled:opacity-50 hover:bg-primary/90 transition-colors"
          >
            {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
            Run Custom Search
          </button>
        </div>
        {taskMsg && (
          <p className="mt-3 text-xs font-semibold">{taskMsg}</p>
        )}
      </div>

      {/* Direct URL Submission */}
      <div className="rounded-xl border border-border bg-card p-6">
        <h3 className="font-extrabold text-base mb-1">Submit Event URL</h3>
        <p className="text-xs text-muted-foreground mb-4">
          Directly submit a specific event URL through the ingestion pipeline.
        </p>
        <form onSubmit={handleSubmitUrl} className="flex gap-3">
          <input
            type="url"
            required
            value={submitUrl}
            onChange={(e) => setSubmitUrl(e.target.value)}
            placeholder="https://lu.ma/some-lucknow-event"
            className="flex-1 rounded-lg border border-border bg-input px-3 py-2 text-sm focus:border-primary focus:outline-none"
          />
          <button
            type="submit"
            disabled={submitting}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-bold disabled:opacity-50 hover:bg-primary/90 transition-colors whitespace-nowrap"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            Submit
          </button>
        </form>
        {submitMsg && <p className="mt-3 text-xs font-semibold">{submitMsg}</p>}
      </div>
    </div>
  );
}
