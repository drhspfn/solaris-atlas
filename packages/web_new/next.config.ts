import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Share previews and readers without JavaScript need metadata in the head
  // of the first response, including requests with ordinary browser agents.
  htmlLimitedBots: /.*/,

  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: '**',
      },
      {
        protocol: 'http',
        hostname: '**',
      },
    ],
  },
  async rewrites() {
    const apiTarget = process.env.API_PROXY_TARGET || process.env.INTERNAL_API_BASE || 'http://localhost:8000';
    return [
      {
        source: '/api/:path*',
        destination: `${apiTarget}/:path*`,
      },
      {
        source: '/docs',
        destination: `${apiTarget}/docs`,
      },
      {
        source: '/openapi.json',
        destination: `${apiTarget}/openapi.json`,
      },
    ];
  },
};

export default nextConfig;
