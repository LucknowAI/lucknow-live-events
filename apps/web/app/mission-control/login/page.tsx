"use client";

import { adminService } from "@/lib/admin-api";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

const SECRET_KEY = "wakandaforever";

function LoginPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const key = searchParams.get("key");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Gate: if key is wrong, show 404-like page
  if (key !== SECRET_KEY) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-background text-foreground">
        <h1 className="text-6xl font-extrabold text-muted-foreground/30">404</h1>
        <p className="text-muted-foreground mt-4">Page not found.</p>
      </div>
    );
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const { access_token } = await adminService.login(email, password);
      localStorage.setItem("admin_token", access_token);
      router.replace(`/mission-control?key=${SECRET_KEY}`);
    } catch {
      setError("Invalid credentials. Check your .env ADMIN_EMAIL and ADMIN_PASSWORD_HASH.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-6">
      <div className="w-full max-w-md bg-card border border-border rounded-2xl p-10 shadow-2xl">
        <div className="mb-8 text-center">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-xl bg-primary/10 mb-4">
            <span className="text-2xl">🛡️</span>
          </div>
          <h1 className="text-2xl font-extrabold">Mission Control</h1>
          <p className="text-sm text-muted-foreground mt-1">Lucknow Tech Events — Admin</p>
        </div>
        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-border bg-input px-3 py-2.5 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="admin@example.com"
            />
          </div>
          <div>
            <label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Password</label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-border bg-input px-3 py-2.5 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          {error && <p className="text-sm text-destructive font-medium">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-primary text-primary-foreground py-2.5 font-bold text-sm disabled:opacity-50 hover:bg-primary/90 transition-colors"
          >
            {loading ? "Authenticating…" : "Sign In"}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function MissionControlLoginPage() {
  return (
    <Suspense>
      <LoginPageInner />
    </Suspense>
  );
}
