"use client";

import { useState } from "react";
import { eventService } from "@/lib/api";
import { Link2, Send, Sparkles } from "lucide-react";
import Link from 'next/link';

export default function SubmitPage() {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error" | "rate_limited">("idle");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus("loading");
    try {
      await eventService.submitEvent({ url });
      setStatus("success");
      setUrl("");
    } catch (err: any) {
      if (err.response?.status === 429) {
        setStatus("rate_limited");
      } else {
        setStatus("error");
      }
    }
  };

  return (
    <div className="max-w-2xl mx-auto py-12 px-6 min-h-[80vh] flex flex-col justify-center">
      <div className="text-center mb-10">
        <div className="inline-flex items-center justify-center p-3 sm:p-4 bg-primary/10 text-primary rounded-2xl mb-4">
          <Sparkles className="w-6 h-6 sm:w-8 sm:h-8" />
        </div>
        <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-foreground mb-4">Submit an Event</h1>
        <p className="text-muted-foreground text-lg max-w-lg mx-auto">
          Help us grow the community directory. Submit any tech event happening in Lucknow or surrounding regions.
        </p>
      </div>

      <div className="bg-card border border-border rounded-2xl p-6 sm:p-10 shadow-2xl">
        {status === "success" ? (
          <div className="text-center py-10 space-y-4">
            <div className="inline-flex bg-green-500/10 text-green-500 p-4 rounded-full mb-4">
              <Send className="w-8 h-8" />
            </div>
            <h3 className="text-2xl font-bold text-foreground">Submission Received!</h3>
            <p className="text-muted-foreground">Thank you. The event has been added to our moderation queue and will be analyzed by our AI agents shortly.</p>
            <button onClick={() => setStatus("idle")} className="mt-4 text-primary font-semibold hover:underline">
              Submit another event
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-6">
            <div className="space-y-2">
              <label htmlFor="url" className="text-sm font-bold text-foreground uppercase tracking-wider">Event URL</label>
              <div className="relative">
                <Link2 className="absolute left-4 top-3.5 h-5 w-5 text-muted-foreground" />
                <input
                  id="url"
                  type="url"
                  required
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://commudle.com/..."
                  className="w-full rounded-xl border border-border bg-input py-3 pl-12 pr-4 text-base focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary transition-all"
                />
              </div>
              <p className="text-xs text-muted-foreground mt-1">We&apos;ll use AI to extract all details automatically.</p>
            </div>

            {status === "error" && (
              <div className="p-4 bg-destructive/10 border border-destructive/20 text-destructive rounded-xl text-sm font-medium">
                Something went wrong submitting your event. Please try again.
              </div>
            )}
            
            {status === "rate_limited" && (
              <div className="p-4 bg-primary/10 border border-primary/20 text-primary rounded-xl text-sm font-medium">
                You&apos;re submitting too quickly! Please wait a while before trying again.
              </div>
            )}

            <button
              type="submit"
              disabled={status === "loading" || !url}
              className="w-full mt-4 flex items-center justify-center gap-2 rounded-xl bg-primary text-primary-foreground px-6 py-4 font-bold text-lg transition-all hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg shadow-primary/20"
            >
              {status === "loading" ? "Submitting..." : "Send to Moderation"} <Send className="w-5 h-5" />
            </button>
          </form>
        )}
      </div>
      
      <div className="mt-8 text-center text-sm text-muted-foreground">
         <Link href="/" className="hover:text-primary transition-colors hover:underline">Return to Home</Link>
      </div>
    </div>
  );
}
