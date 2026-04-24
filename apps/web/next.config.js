/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
    ],
  },
  async rewrites() {
    // On Vercel, the backend service is mounted at /_/backend by experimentalServices.
    // In local Docker dev, INTERNAL_API_URL points to the api container.
    // In local non-Docker dev, we fall back to localhost:8000.
    const isVercel = process.env.VERCEL === "1";

    const backendBase = isVercel
      ? "/_/backend"
      : (process.env.INTERNAL_API_URL?.replace(/\/api\/v1$/, "") ||
         process.env.NEXT_PUBLIC_API_URL?.replace(/\/api\/v1$/, "") ||
         "http://localhost:8000");

    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendBase}/api/v1/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
