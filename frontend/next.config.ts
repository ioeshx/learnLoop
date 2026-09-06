import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  experimental: {
    // The TypeScript compiler API is more portable than Next.js' detached CLI
    // process and still performs the same project-wide type check during builds.
    useTypeScriptCli: false,
  },
};

export default nextConfig;
