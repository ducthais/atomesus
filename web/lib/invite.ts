/**
 * Link mời Reviewer (USER-SCOPE): Engineer tạo link /invite/<token>, người nhận đăng ký Reviewer hoặc chấp nhận bằng
 * tài khoản Reviewer có sẵn. Chỉ logic thuần (không gọi backend) để test được bằng node.
 */

export const INVITE_DAYS = 7; // = INVITE_SECONDS của backend
export const INVITE_PREFIX = '/invite/';
export const REVIEW_PATH = '/runs/review';
export const INVITE_INVALID = 'Link mời không hợp lệ. Kiểm tra lại link đã nhận hoặc xin người mời gửi link mới.';
export const INVITE_GONE = 'Link mời đã hết hạn hoặc đã được dùng. Hãy xin người mời gửi link mới.';
export const INVITE_ENGINEER = 'Tài khoản Engineer không nhận được lời mời Reviewer. Hãy đăng xuất rồi đăng ký tài khoản Reviewer từ link này, hoặc đăng nhập bằng tài khoản Reviewer.';
export const INVITE_OTHER_ROLE = 'Chỉ tài khoản Reviewer nhận được lời mời duyệt. Hãy đăng xuất rồi đăng nhập bằng tài khoản Reviewer.';
export const INVITE_SESSION = 'Phiên đăng nhập đã hết hạn. Đăng nhập lại rồi mở lại link mời.';

/** POST /pipeline/invites trả về. */
export type CreatedInvite = { token: string; path: string; expires_at: string };
/** GET /pipeline/reviewers: Reviewer hệ thống không có username; cặp demo không gỡ được. */
export type LinkedReviewer = { id: string; username: string | null; name: string | null; linked_at: number | null; demo: boolean };
export type InviteView = 'invalid' | 'signup' | 'accept' | 'engineer' | 'other';

/** Token do backend sinh (token_urlsafe), backend nhận 16–128 ký tự. Kiểm trước để không gửi rác lên backend. */
export const isInviteToken = (token: string) => /^[A-Za-z0-9_-]{16,128}$/.test(token);

/** Token trong đường dẫn /invite/<token>; null nếu không phải trang mời. */
export function inviteTokenFromPath(path: string | null | undefined): string | null {
  const match = /^\/invite\/([^/?#]+)\/?$/.exec(path ?? '');
  if (!match) return null;
  try { return decodeURIComponent(match[1]); } catch { return ''; }
}

/** Trang mời hiện gì cho người đang xem (null: chưa đăng nhập). */
export function inviteView(role: string | null | undefined, token: string): InviteView {
  if (!isInviteToken(token)) return 'invalid';
  if (!role) return 'signup';
  if (role === 'Reviewer') return 'accept';
  return role === 'Engineer' ? 'engineer' : 'other';
}

/** Link đầy đủ để gửi cho người nhận: origin trang hiện tại + đường dẫn backend trả. */
export function inviteUrl(origin: string, invite: Pick<CreatedInvite, 'token' | 'path'>): string {
  const path = invite.path?.startsWith(INVITE_PREFIX) ? invite.path : `${INVITE_PREFIX}${invite.token}`;
  return `${origin.replace(/\/+$/, '')}${path}`;
}

/** Lỗi backend khi dùng link mời → câu tiếng Việt dễ hiểu (404 token sai, 410 hết hạn / đã dùng). */
export function inviteErrorMessage(status: number, detail: string): string {
  if (status === 404) return INVITE_INVALID;
  if (status === 410) return INVITE_GONE;
  if (status === 401) return INVITE_SESSION;
  return detail || 'Thao tác với link mời thất bại. Vui lòng thử lại.';
}

/** Chỉ cho chuyển về đường dẫn nội bộ sau đăng nhập; mọi giá trị khác (URL ngoài, //host) về trang chủ. */
export function safeNext(value: unknown): string {
  if (typeof value !== 'string' || value.length > 512) return '/';
  if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/\\') || /[\s\\]/.test(value)) return '/';
  return value;
}

/** Dòng "tài khoản" trong danh sách Reviewer. */
export const reviewerAccount = (reviewer: LinkedReviewer) => reviewer.username ?? (reviewer.demo ? 'Tài khoản demo' : 'Tài khoản hệ thống');
