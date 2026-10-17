import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  devIndicators: false,
  reactStrictMode: true,
  outputFileTracingRoot: path.join(__dirname),
  typescript: {
    // Workaround: Next 16.1.3 ships broken type exports
    // (e.g. PrefetchForTypeCheckInternal) that break type checking
    // even when the project's own code is fine. Skip in build; tsc --noEmit
    // still works for local type-check via `npm run typecheck`.
    ignoreBuildErrors: true,
  },
  // eslint config goes in eslint config file, not next.config in Next 16
};

export default nextConfig;
