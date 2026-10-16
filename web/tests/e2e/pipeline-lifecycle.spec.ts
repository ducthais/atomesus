import { expect, test, type Page } from '@playwright/test';

async function login(page: Page, username: 'engineer' | 'reviewer', password: string) {
  await page.goto('/explore'); // `/` là landing công khai; trang cần đăng nhập hiện màn đăng nhập rồi quay lại đúng trang
  await page.getByLabel('Tên đăng nhập').fill(username);
  await page.getByLabel('Mật khẩu').fill(password);
  await page.getByRole('button', { name: 'Đăng nhập' }).click();
  await expect(page.getByRole('heading', { name: 'Khám phá dữ liệu' })).toBeVisible();
}

test('đăng nhập → tạo mẻ pipeline → phê duyệt → Deploy', async ({ page, context }) => {
  await login(page, 'engineer', 'Engineer@123');
  await page.goto('/runs/new');

  await expect(page.locator('.request-form')).toHaveAttribute('data-hydrated', 'true');
  await page.getByRole('tab', { name: 'Nhiều pipeline' }).click();
  await page.getByRole('button', { name: 'Kết nối' }).click();
  await expect(page.getByText('1 bảng · đã chọn 0')).toBeVisible();
  await page.getByRole('checkbox', { name: /orders/ }).check();
  await page.getByRole('textbox', { name: 'Yêu cầu pipeline 1' }).fill('Tổng doanh thu theo ngày');
  await page.getByRole('textbox', { name: 'Yêu cầu pipeline 2' }).fill('Số đơn hàng theo ngày');
  await expect(page.getByRole('status').filter({ hasText: 'Ước tính giữ chỗ' })).toContainText('$0.1000');
  await page.getByRole('button', { name: 'Tạo cả mẻ' }).click();

  await expect(page).toHaveURL(/\/runs\/batch001\?batch=2&estimated=0\.1000$/);
  await expect(page.getByText('Đã tạo trọn mẻ 2 pipeline')).toBeVisible();
  const runUrl = new URL(page.url()).pathname;

  await context.clearCookies();
  await login(page, 'reviewer', 'Reviewer@123');
  await page.goto(`${runUrl}?tab=overview`);
  await expect(page.getByRole('button', { name: 'Phê duyệt' })).toBeEnabled();
  await page.getByRole('button', { name: 'Phê duyệt' }).click();

  await expect(page.getByRole('button', { name: 'Deploy' })).toBeEnabled();
  await page.getByRole('button', { name: 'Deploy' }).click();
  await expect(page.getByText('Đã mở PR')).toBeVisible();
  await expect(page.getByRole('link', { name: /Xem pull request #42/ })).toBeVisible();
});
