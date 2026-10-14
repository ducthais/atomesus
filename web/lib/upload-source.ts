import { cookies } from 'next/headers';
import { MOCK_UPLOADS, type UserUpload } from './uploads.ts';

const UPLOADS_COOKIE = 'catalog_user_uploads';

export async function mockUserUploads(ownerId: string): Promise<UserUpload[]> {
  const value = (await cookies()).get(UPLOADS_COOKIE)?.value;
  if (value) {
    try {
      const saved = JSON.parse(value) as { owner: string; items: UserUpload[] };
      if (saved.owner === ownerId && Array.isArray(saved.items)) return saved.items;
    } catch { /* Cookie mock không hợp lệ: dùng lại ví dụ. */ }
  }
  return MOCK_UPLOADS.map(item => ({ ...item, owner_id: ownerId }));
}

export async function saveMockUserUploads(ownerId: string, items: UserUpload[]): Promise<void> {
  const value = JSON.stringify({ owner: ownerId, items });
  if (encodeURIComponent(value).length > 3800) throw new Error('Dữ liệu mock đã đầy trong phiên. Xóa file minh họa cũ rồi thử lại.');
  (await cookies()).set(UPLOADS_COOKIE, value, {
    path: '/', httpOnly: true, sameSite: 'lax', secure: process.env.NODE_ENV === 'production', maxAge: 28800,
  });
}
