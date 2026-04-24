"use client";

import { useState, useEffect } from "react";
import { Menu, X } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { usePathname } from "next/navigation";

export function ClientLayoutWrapper({ children }: { children: React.ReactNode }) {
  // Default to open on larger screens, closed on mobile initially
  const [isOpen, setIsOpen] = useState(true);
  const [isMounted, setIsMounted] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    setIsMounted(true);
    // Auto-close sidebar on mobile after navigation
    if (window.innerWidth < 768) {
      setIsOpen(false);
    }
  }, [pathname]);

  // Avoid hydration mismatch by not rendering the sliding state until mounted
  if (!isMounted) {
    return (
      <div className="flex h-screen overflow-hidden">
        <aside className="hidden md:block w-64 border-r border-border bg-card flex-shrink-0">
          <Sidebar />
        </aside>
        <div className="flex min-w-0 flex-1 flex-col relative w-full">
          <header className="flex flex-shrink-0 h-14 items-center gap-4 border-b border-border bg-card px-4 md:px-6">
            <div className="w-8 h-8 rounded-md bg-muted animate-pulse" />
            <span className="font-bold text-primary tracking-wide">Lucknow Tech Events</span>
          </header>
          <main className="flex-1 overflow-y-auto w-full relative">{children}</main>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Mobile Overlay */}
      {isOpen && (
        <div 
          className="fixed inset-0 bg-background/80 backdrop-blur-sm z-40 md:hidden"
          onClick={() => setIsOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed md:relative z-50 h-full w-64 border-r border-border bg-card flex-shrink-0 transform transition-transform duration-300 ease-in-out ${
          isOpen ? "translate-x-0" : "-translate-x-full md:hidden md:-ml-64 md:translate-x-0"
        } ${!isOpen && "hidden md:block md:-ml-64"}`}
        style={{
          marginLeft: isOpen ? 0 : -256, // 64 * 4px = 256px
        }}
      >
        <Sidebar className="h-full" />
      </aside>

      {/* Main Content Area */}
      <div className="flex min-w-0 flex-1 flex-col relative w-full transition-all duration-300 ease-in-out">
        <header className="flex flex-shrink-0 h-14 items-center gap-4 border-b border-border bg-card px-4 md:px-6 sticky top-0 z-30">
          <button
            onClick={() => setIsOpen(!isOpen)}
            className="p-2 -ml-2 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors focus:outline-none focus:ring-2 focus:ring-primary"
            aria-label="Toggle Sidebar"
          >
            {isOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
          
          <span className="font-bold text-primary tracking-wide">Lucknow Tech Events</span>
        </header>

        <main className="flex-1 overflow-y-auto w-full relative">
          {children}
        </main>
      </div>
    </div>
  );
}
