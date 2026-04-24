"use client";

import { useState, useEffect, ReactNode } from "react";
import { Info } from "lucide-react";

interface ComingSoonButtonProps {
  children: ReactNode;
  className?: string;
}

export function ComingSoonButton({ children, className }: ComingSoonButtonProps) {
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 3000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  return (
    <>
      <a
        href="#"
        onClick={(e) => {
          e.preventDefault();
          setToast("Feature coming soon");
        }}
        className={className}
      >
        {children}
      </a>

      {toast && (
        <div className="fixed bottom-6 right-6 flex items-center gap-2 bg-primary text-primary-foreground px-5 py-3 rounded-lg shadow-xl font-medium text-sm z-50 animate-in fade-in slide-in-from-bottom-5">
          <Info className="w-4 h-4" />
          {toast}
        </div>
      )}
    </>
  );
}
