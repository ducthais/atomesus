import type { NextConfig } from 'next';

const backend = (process.env.PIPELINE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const config: NextConfig = {
  allowedDevOrigins: ['127.0.0.1'],
  // POST /api/v1/agent/runs chạy đồng bộ ~30 giây; mặc định proxy ngắt sau 30 giây.
  experimental: { proxyTimeout: 300_000 },
  async rewrites() {
    return [{ source: '/api/v1/:path*', destination: `${backend}/api/v1/:path*` }];
  },
};
export default config;
