/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
    ],
  },
  async rewrites() {
    // Determines where the FastAPI backend lives, for all three environments:
    //
    // 1. Docker dev:  INTERNAL_API_URL=http://api:8000/api/v1 is set by docker-compose.
    //                 The Next.js container resolves "api" via Docker's internal DNS.
    //
    // 2. Production (Vercel + Cloud Run):
    //                 NEXT_PUBLIC_API_URL=https://<cloud-run-host>/api/v1 is set
    //                 in Vercel's environment variables dashboard.
    //                 Next.js rewrites proxy the browser's /api/v1/* calls to Cloud Run,
    //                 avoiding CORS issues and hiding the backend URL.
    //
    // 3. Plain local dev (no Docker):
    //                 Neither env var is set → fall back to localhost:8000.

    const backendBase = process.env.INTERNAL_API_URL
      // Docker: strip /api/v1 suffix to get the container base host
      ? process.env.INTERNAL_API_URL.replace(/\/api\/v1$/, "")
      // Production / plain-local: use the public URL or localhost
      : (process.env.NEXT_PUBLIC_API_URL?.replace(/\/api\/v1$/, "") || "http://localhost:8000");

    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendBase}/api/v1/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
