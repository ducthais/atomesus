import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSnapshot } from '../lib/catalog/live.ts';
import { LIMITS } from '../lib/catalog/mock.ts';
import { limitLines, mockQuota, type Quota } from '../lib/limits.ts';

const NOW = new Date('2026-10-16T09:30:00Z');
const noFileName = (text: string) => assert.doesNotMatch(text, /\.ya?ml|budgets|objectives|domains|per_run|per_day|max_/);

test('giới hạn của Engineer: tiền và số run đã dùng / trần hôm nay, trần mỗi run, bằng câu thường', () => {
  const lines = limitLines(LIMITS, mockQuota('Engineer', NOW));
  const byId = Object.fromEntries(lines.map(line => [line.id, line]));

  assert.deepEqual(lines.map(line => line.id), ['usd', 'runs', 'per_run', 'reset']);
  assert.match(byId.usd.text, /\$0\.12.*\$0\.30.*còn \$0\.18/);
  assert.equal(Math.round(byId.usd.pct ?? 0), 40);
  assert.match(byId.runs.text, /đã tạo 4 run, tối đa 20 run mỗi ngày; còn 16 lượt/);
  assert.equal(byId.runs.pct, 20);
  assert.match(byId.per_run.text, /gọi AI tối đa 6 lần, tự sửa Spec tối đa 2 lần và trả lời tối đa 20 câu hỏi chat/);
  assert.match(byId.reset.text, /07:00/, '0 giờ UTC ngày sau = 7 giờ sáng giờ Việt Nam');
  for (const line of lines) noFileName(line.text);
});

test('giới hạn khi không có trần riêng (Admin) hoặc chưa đọc được quota', () => {
  const free = Object.fromEntries(limitLines(LIMITS, mockQuota('Admin', NOW)).map(line => [line.id, line]));
  assert.equal(free.usd.pct, undefined);
  assert.match(free.usd.text, /không có trần chi phí riêng.*\$2\.00/);
  assert.match(free.runs.text, /không giới hạn số run/);

  const unknown = limitLines(LIMITS, null);
  assert.deepEqual(unknown.map(line => line.id), ['usd', 'per_run']);
  assert.match(unknown[0].text, /Chưa đọc được/);
  for (const line of unknown) noFileName(line.text);
});

test('giới hạn: số tiền rất nhỏ vẫn hiện, phần trăm không vượt 100, giờ đặt lại hỏng thì bỏ dòng', () => {
  const quota: Quota = { ...mockQuota('Engineer', NOW), resets_at: 'không hợp lệ' };
  const lines = limitLines(LIMITS, { ...quota, usd: { used: 0.0042, limit: 0.003, remaining: 0 }, runs: { used: 25, limit: 20, remaining: 0 } });
  assert.match(lines[0].text, /\$0\.0042/);
  assert.equal(lines[0].pct, 100);
  assert.equal(lines[1].pct, 100);
  assert.ok(lines.every(line => line.id !== 'reset'));
});

test('catalog live: /policy không có file (vai không phải Admin) vẫn dựng được, giữ trần', () => {
  const catalog = { domains: {}, conformed_dimensions: [] };
  for (const policy of [{ limits: LIMITS }, { files: {}, limits: LIMITS }]) {
    const snapshot = buildSnapshot(catalog, [], policy, []);
    assert.deepEqual(snapshot.policy.files, []);
    assert.deepEqual(snapshot.policy.limits, LIMITS);
  }
});
