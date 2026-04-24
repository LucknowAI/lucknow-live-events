import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/react";
import { Inter } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });

import { ClientLayoutWrapper } from "@/components/ClientLayoutWrapper";

export const metadata: Metadata = {
  title: { default: "Lucknow Tech Events", template: "%s | Lucknow Tech Events" },
  description: "One place for upcoming tech events in Lucknow.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${inter.variable} font-sans bg-background text-foreground antialiased`}
      >
        <ClientLayoutWrapper>{children}</ClientLayoutWrapper>
        <Analytics />
      </body>
    </html>
  );
}
