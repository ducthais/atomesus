import type { NextConfig } from 'next';

// UI gọi `/api/*`; Next.js chuyển tiếp sang FastAPI (api/main.py) để khỏi phải cấu hình CORS.
const CP_API_URL = process.env.CP_API_URL ?? 'http://127.0.0.1:8000';

// NEXT_OUTPUT=export: xuất tĩnh cho bản demo một file (scripts/build-demo-html.mjs); không có proxy /api.
const staticExport = process.env.NEXT_OUTPUT === 'export';

const config: NextConfig = {
  // Nén gzip làm SSE (`/api/runs/{id}/events`) bị dồn buffer, sự kiện không tới trình duyệt theo thời gian thực.
  compress: false,
  ...(staticExport
    ? { output: 'export', distDir: '.next-demo' }
    : {
        async rewrites() {
          return [{ source: '/api/:path*', destination: `${CP_API_URL}/:path*` }];
        },
      }),
};

export default config;
