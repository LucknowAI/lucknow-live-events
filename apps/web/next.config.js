/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
    ],
  },
  async rewrites() {
    // Three environments:
    // 1. Vercel prod   — backend mounted at /_/backend via experimentalServices
    // 2. Docker dev    — INTERNAL_API_URL=http://api:8000/api/v1 (container hostname)
    // 3. Plain local / Cloud Run frontend — NEXT_PUBLIC_API_URL is the full public URL
    const isVercel = process.env.VERCEL === "1";

    let backendBase;
    if (isVercel) {
      backendBase = "/_/backend";
    } else if (process.env.INTERNAL_API_URL) {
      // Docker dev: strip /api/v1 suffix to get the base host
      backendBase = process.env.INTERNAL_API_URL.replace(/\/api\/v1$/, "");
    } else if (
      process.env.NEXT_PUBLIC_API_URL &&
      !process.env.NEXT_PUBLIC_API_URL.includes("localhost")
    ) {
      // External backend (Cloud Run, Railway, etc.) — proxy to the real URL
      backendBase = process.env.NEXT_PUBLIC_API_URL.replace(/\/api\/v1$/, "");
    } else {
      // Bare local dev: Next.js dev server and FastAPI both on localhost
      backendBase = "http://localhost:8000";
    }

    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendBase}/api/v1/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
