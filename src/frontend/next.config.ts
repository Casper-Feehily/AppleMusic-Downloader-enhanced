import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

export default function nextConfig(phase: string): NextConfig {
  if (phase === PHASE_DEVELOPMENT_SERVER) {
    return {
      images: { unoptimized: true },
      async rewrites() {
        return [
          {
            source: "/api/:path*",
            destination: "http://127.0.0.1:8000/api/:path*",
          },
        ];
      },
    };
  }

  return {
    output: "export",
    images: { unoptimized: true },
  };
}
