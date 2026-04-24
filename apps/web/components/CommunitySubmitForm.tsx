"use client";

import { useState } from "react";
import { PlusCircle, Info, Send } from "lucide-react";

export function CommunitySubmitForm() {
  const [toast, setToast] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [link, setLink] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name || !link) return;
    
    // Simulating sending to moderation
    setToast("Sent to moderation!");
    setName("");
    setLink("");
    
    setTimeout(() => {
      setToast(null);
    }, 3000);
  };

  return (
    <div className="bg-card border border-border rounded-2xl p-6 mt-12 relative overflow-hidden">
      <div className="flex items-center gap-3 mb-4">
        <PlusCircle className="w-6 h-6 text-primary" />
        <h2 className="text-xl font-bold">Did we miss a community?</h2>
      </div>
      <p className="text-sm text-muted-foreground mb-6">
        Add it here! Your submission will be sent to moderation and added to the directory upon approval.
      </p>
      
      <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-4">
        <div className="flex-1">
          <input
            type="text"
            placeholder="Community Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            className="w-full h-10 rounded-xl border border-border bg-background px-4 text-sm font-medium focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary shadow-sm transition-all"
          />
        </div>
        <div className="flex-1">
          <input
            type="url"
            placeholder="Community Link"
            value={link}
            onChange={(e) => setLink(e.target.value)}
            required
            className="w-full h-10 rounded-xl border border-border bg-background px-4 text-sm font-medium focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary shadow-sm transition-all"
          />
        </div>
        <button
          type="submit"
          className="h-10 px-6 rounded-xl bg-primary text-primary-foreground text-sm font-bold shadow-md hover:bg-primary/90 transition-all flex items-center justify-center gap-2"
        >
          <Send className="w-4 h-4" /> Submit
        </button>
      </form>

      {toast && (
        <div className="absolute top-6 right-6 flex items-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-lg shadow-lg font-medium text-sm z-50 animate-in fade-in">
          <Info className="w-4 h-4" />
          {toast}
        </div>
      )}
    </div>
  );
}
