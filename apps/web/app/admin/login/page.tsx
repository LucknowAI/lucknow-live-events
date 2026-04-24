"use client";

import { adminService } from "@/lib/admin-api";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function AdminLoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const { access_token } = await adminService.login(email, password);
      localStorage.setItem("admin_token", access_token);
      router.replace("/admin");
    } catch {
      setError("Invalid credentials or admin not configured.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-md mx-auto py-20 px-6">
      <h1 className="text-2xl font-bold mb-2">Admin login</h1>
      <p className="text-sm text-muted-foreground mb-8">JWT access to crawl sources and moderation.</p>
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="text-xs font-semibold text-muted-foreground uppercase">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-input px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="text-xs font-semibold text-muted-foreground uppercase">Password</label>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-md border border-border bg-input px-3 py-2 text-sm"
          />
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-md bg-primary text-primary-foreground py-2 font-semibold disabled:opacity-50"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="mt-8 text-center text-sm text-muted-foreground">
        <Link href="/" className="hover:text-primary">
          ← Back to site
        </Link>
      </p>
    </div>
  );
}
