import test from 'node:test';
import assert from 'node:assert/strict';
import { INVITE_GONE, INVITE_INVALID, INVITE_SESSION, inviteErrorMessage, inviteTokenFromPath, inviteUrl, inviteView, isInviteToken, reviewerAccount, safeNext } from '../lib/invite.ts';
import { LINKED_REVIEWERS } from '../lib/catalog/mock.ts';

const TOKEN = `test-invite-token-${'x'.repeat(24)}`; // token giả, cùng bảng ký tự với secrets.token_urlsafe

test('lời mời: token hợp lệ đúng dạng token_urlsafe 16–128 ký tự', () => {
  assert.equal(isInviteToken(TOKEN), true);
  assert.equal(isInviteToken('a'.repeat(16)), true);
  assert.equal(isInviteToken('a'.repeat(15)), false);
  assert.equal(isInviteToken('a'.repeat(129)), false);
  for (const bad of ['', 'abc def ghijklmnop', 'abc/defghijklmnopq', 'abc%2Fdefghijklmnop', `${TOKEN}\n`]) assert.equal(isInviteToken(bad), false, bad);
});

test('lời mời: lấy token từ đường dẫn /invite/<token>', () => {
  assert.equal(inviteTokenFromPath(`/invite/${TOKEN}`), TOKEN);
  assert.equal(inviteTokenFromPath(`/invite/${TOKEN}/`), TOKEN);
  assert.equal(inviteTokenFromPath('/invite/abc%20def'), 'abc def');
  assert.equal(inviteTokenFromPath('/invite/%E0%A4%A'), ''); // mã hóa hỏng: coi là token sai, vẫn là trang mời
  for (const path of ['/', '/invite', '/invite/', '/invite/a/b', '/runs/invite/x', null, undefined]) assert.equal(inviteTokenFromPath(path), null, String(path));
});

test('lời mời: trang hiện đúng trạng thái theo vai trò người xem', () => {
  assert.equal(inviteView(null, TOKEN), 'signup');
  assert.equal(inviteView(undefined, TOKEN), 'signup');
  assert.equal(inviteView('Reviewer', TOKEN), 'accept');
  assert.equal(inviteView('Engineer', TOKEN), 'engineer');
  assert.equal(inviteView('Admin', TOKEN), 'other');
  assert.equal(inviteView('Steward', TOKEN), 'other');
  for (const role of [null, 'Reviewer', 'Engineer']) assert.equal(inviteView(role, 'short'), 'invalid');
});

test('lời mời: link đầy đủ = origin hiện tại + đường dẫn backend trả', () => {
  assert.equal(inviteUrl('https://atomesus.vn', { token: TOKEN, path: `/invite/${TOKEN}` }), `https://atomesus.vn/invite/${TOKEN}`);
  assert.equal(inviteUrl('http://localhost:3000/', { token: TOKEN, path: `/invite/${TOKEN}` }), `http://localhost:3000/invite/${TOKEN}`);
  // Đường dẫn lạ từ backend không được dẫn ra ngoài: dựng lại từ token.
  assert.equal(inviteUrl('https://atomesus.vn', { token: TOKEN, path: '//evil.example/x' }), `https://atomesus.vn/invite/${TOKEN}`);
});

test('lời mời: lỗi backend thành câu tiếng Việt dễ hiểu', () => {
  assert.equal(inviteErrorMessage(404, 'Link mời không hợp lệ.'), INVITE_INVALID);
  assert.equal(inviteErrorMessage(410, 'gone'), INVITE_GONE);
  assert.equal(inviteErrorMessage(401, 'Not authenticated'), INVITE_SESSION);
  assert.equal(inviteErrorMessage(400, 'Chỉ tài khoản Reviewer nhận được lời mời duyệt.'), 'Chỉ tài khoản Reviewer nhận được lời mời duyệt.');
  assert.match(inviteErrorMessage(500, ''), /thất bại/);
});

test('lời mời: chỉ chuyển hướng về đường dẫn nội bộ sau đăng nhập', () => {
  assert.equal(safeNext(`/invite/${TOKEN}`), `/invite/${TOKEN}`);
  assert.equal(safeNext('/runs/review'), '/runs/review');
  for (const bad of [null, undefined, '', 'https://evil.example', '//evil.example', '/\\evil.example', '/a b', '/a\\b', 'javascript:alert(1)', `/${'a'.repeat(600)}`]) {
    assert.equal(safeNext(bad), '/', String(bad));
  }
});

test('lời mời: mock có cặp demo không gỡ được và Reviewer liên kết có tài khoản', () => {
  assert.equal(LINKED_REVIEWERS.filter(item => item.demo).length, 1);
  assert.equal(reviewerAccount(LINKED_REVIEWERS.find(item => item.demo)!), 'Tài khoản demo');
  assert.equal(reviewerAccount(LINKED_REVIEWERS.find(item => !item.demo)!), 'hoa.vu');
  assert.equal(reviewerAccount({ id: 'an', username: null, name: 'An', linked_at: 1, demo: false }), 'Tài khoản hệ thống');
});
