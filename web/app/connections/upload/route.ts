import { cookies } from 'next/headers';
import { BACKEND, MOCK, SESSION_COOKIE, currentUser } from '@/lib/catalog/source';
import { proxyUpload } from '@/lib/upload-api';

export async function POST(request: Request) {
  const actor = await currentUser();
  if (!actor) return Response.json({ detail: 'Đăng nhập để tải file nguồn.' }, { status: 401 });
  if (actor.role !== 'Engineer') return Response.json({ detail: 'Chỉ Engineer được tải file nguồn.' }, { status: 403 });
  if (MOCK) return Response.json({ detail: 'Dùng thao tác tải file mô phỏng trong giao diện.' }, { status: 422 });
  const origin = request.headers.get('origin');
  const host = request.headers.get('x-forwarded-host') ?? request.headers.get('host') ?? new URL(request.url).host;
  if (origin) {
    try {
      if (new URL(origin).host !== host) return Response.json({ detail: 'Nguồn yêu cầu không hợp lệ.' }, { status: 403 });
    } catch { return Response.json({ detail: 'Nguồn yêu cầu không hợp lệ.' }, { status: 403 }); }
  }
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return Response.json({ detail: 'Phiên đăng nhập đã hết hạn.' }, { status: 401 });
  return proxyUpload(request, BACKEND, token);
}
