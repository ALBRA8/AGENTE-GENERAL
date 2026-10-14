import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Disable the Next.js DevTools indicator (the "N" overlay button
  // that appears in the bottom-left during development). It obstructs
  // the dashboard's content and provides no value to operators.
  devIndicators: false,
  reactStrictMode: true,
};

export default nextConfig;
