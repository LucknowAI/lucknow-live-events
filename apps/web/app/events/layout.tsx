import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "All events",
  description: "Browse upcoming tech meetups, workshops, and hackathons in Lucknow. Filter by topic, locality, and more.",
  openGraph: {
    title: "All events | Lucknow Tech Events",
    description: "Browse upcoming tech events in Lucknow.",
  },
};

export default function EventsLayout({ children }: { children: React.ReactNode }) {
  return children;
}
