import type { NextConfig } from 'next';

const backend = (process.env.PIPELINE_BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const config: NextConfig = {
  async rewrites() {
    return [{ source: '/api/v1/:path*', destination: `${backend}/api/v1/:path*` }];
  },
};
export default config;
