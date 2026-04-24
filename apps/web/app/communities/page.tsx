import { facetService } from "@/lib/api";
import { Users, ExternalLink, Star } from "lucide-react";
import Link from "next/link";
import { CommunitySubmitForm } from "@/components/CommunitySubmitForm";

export const metadata = {
  title: "Communities",
  description: "Communities and groups with published events in Lucknow.",
};

const FEATURED_COMMUNITIES = [
  { name: "GDG Lucknow", url: "https://gdg.community.dev/gdg-lucknow/" },
  { name: "Lucknow AI Labs", url: "http://lucknowai.org" },
  { name: "AI Community Lucknow (TFUG)", url: "https://aicommunity.lucknow.dev" },
  { name: "GDG on Campus - SRMCEM", url: "https://gdg.community.dev/gdg-on-campus-shri-ramswaroop-memorial-college-of-engineering-and-management-lucknow-india/" },
  { name: "GDG on Campus - BBDITM", url: "https://gdg.community.dev/gdg-on-campus-babu-banarasi-das-institute-of-technology-and-management-lucknow-india/" },
  { name: "GDG on Campus - BNCET", url: "https://gdg.community.dev/gdg-on-campus-bn-college-of-engineering-technology-lucknow-india/" },
  { name: "GDG on Campus - BBDNIIT", url: "https://gdg.community.dev/gdg-on-campus-babu-banarasi-das-northern-india-institute-of-technology-lucknow-india/" },
  { name: "GDG on Campus - Bansal IET", url: "https://gdg.community.dev/gdg-on-campus-bansal-institute-of-engineering-technology-lucknow-india/" },
  { name: "GDG on Campus - Integral University", url: "https://developers.google.com/profile/badges/community/gdg/chapter/member/gdg-on-campus-integral-university-lucknow-india" },
  { name: "GDG on Campus - IIIT Lucknow", url: "https://gdg.community.dev/gdg-on-campus-indian-institute-of-information-technology-lucknow-india/" },
  { name: "AI Club - LPCPS", url: "https://lpcps.org.in/Clubs#AI-CLUB" }
];

export default async function CommunitiesPage() {
  let items: Awaited<ReturnType<typeof facetService.getCommunities>> = [];
  try {
    items = await facetService.getCommunities();
  } catch {
    /* empty */
  }

  // Filter out featured communities from the "More" section to avoid duplication if they exactly match
  const featuredNamesLower = FEATURED_COMMUNITIES.map(c => c.name.toLowerCase());
  const moreCommunities = items.filter(c => !featuredNamesLower.includes(c.name.toLowerCase()));

  return (
    <div className="max-w-6xl mx-auto py-12 px-6">
      <div className="flex items-center gap-3 mb-10">
        <Users className="w-8 h-8 text-primary" />
        <div>
          <h1 className="text-4xl font-extrabold tracking-tight">Communities</h1>
          <p className="text-muted-foreground mt-1">Discover tech groups driving the Lucknow developer ecosystem.</p>
        </div>
      </div>

      <div className="mb-12">
        <div className="flex items-center gap-2 mb-6">
          <Star className="w-5 h-5 text-yellow-500 fill-yellow-500" />
          <h2 className="text-2xl font-bold">Featured Communities</h2>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {FEATURED_COMMUNITIES.map((c) => (
            <a
              key={c.name}
              href={c.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex flex-col justify-between rounded-2xl border border-border bg-card p-6 hover:border-primary hover:shadow-lg transition-all group"
            >
              <div>
                <h3 className="font-bold text-lg text-foreground group-hover:text-primary transition-colors pr-6">
                  {c.name}
                </h3>
              </div>
              <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mt-4 uppercase tracking-wider group-hover:text-primary transition-colors">
                <ExternalLink className="w-3.5 h-3.5" /> Visit Community
              </div>
            </a>
          ))}
        </div>
      </div>

      <div className="w-full h-px bg-border my-12" />

      <div className="mb-8">
        <h2 className="text-2xl font-bold mb-6">More Communities</h2>
        {moreCommunities.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border p-12 text-center bg-card">
            <p className="text-muted-foreground">No other communities indexed yet.</p>
          </div>
        ) : (
          <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {moreCommunities.map((c) => (
              <li key={c.name}>
                <Link
                  href={`/events?community=${encodeURIComponent(c.name)}`}
                  className="flex justify-between items-center rounded-xl border border-border bg-card px-5 py-4 hover:border-primary transition-all shadow-sm"
                >
                  <span className="font-bold text-sm truncate pr-4">{c.name}</span>
                  <span className="text-xs font-extrabold bg-muted text-muted-foreground px-2.5 py-1 rounded-full whitespace-nowrap">
                    {c.count} events
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>

      <CommunitySubmitForm />
    </div>
  );
}
