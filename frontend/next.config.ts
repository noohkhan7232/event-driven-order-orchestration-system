import type { NextConfig } from "next";
const config: NextConfig = {
  // Keep standalone builds practical on 8 GB Windows/Docker developer machines.
  experimental: { cpus: 1 },
  output: "standalone",
  poweredByHeader: false,
  // URL paths can contain public tracking capabilities; do not log incoming URLs.
  logging: { incomingRequests: false },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
        ],
      },
    ];
  },
};
export default config;
