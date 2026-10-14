import { expect, test, type Page } from '@playwright/test';

// Run có sẵn trong tests/e2e/fixture-backend.mjs (seeded).
const LONG_MODEL = 'fct_doanh_thu_theo_ngay_cua_tung_cua_hang_khu_vuc_mien_nam_va_mien_bac_sau_khi_tru_hoan_tien';
const TABLE = 'don_hang_da_xac_nhan_kem_thanh_toan';

async function login(page: Page) {
  await page.goto('/');
  await page.getByLabel('Tên đăng nhập').fill('reviewer');
  await page.getByLabel('Mật khẩu').fill('Reviewer@123');
  await page.getByRole('button', { name: 'Đăng nhập' }).click();
  await expect(page.getByRole('heading', { name: 'Khám phá dữ liệu' })).toBeVisible();
}

const summaryPanel = (page: Page) => page.locator('section.panel', { has: page.getByRole('heading', { name: 'Pipeline này làm gì' }) });

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`màn duyệt ${viewport.width}px: tóm tắt ở đầu, trước tab và nút duyệt`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    await login(page);
    await page.goto('/runs/summary1?tab=overview');

    const panel = summaryPanel(page);
    await expect(panel).toBeVisible();
    await expect(panel.locator('li')).toHaveText([
      `Đọc 2 bảng: kho_ban_hang_mien_dong_postgres.${TABLE}, kho_ban_hang_mien_tay_postgres.${TABLE}`,
      `Ra bảng ${LONG_MODEL} khoảng 1.234.567 dòng`,
      'Chạy theo lịch mỗi giờ vào phút 0, 45',
      'Chi phí ước tính ~$0.000001 mỗi lần chạy',
      'Optimizer: nhanh hơn 34,8% (0,2155 s → 0,1405 s)',
    ]);
    await expect(page.getByRole('button', { name: 'Phê duyệt' })).toBeVisible();

    // Khối tóm tắt đứng trước thanh tab và khung duyệt; các phần còn lại giữ thứ tự cũ.
    const order = await page.evaluate(() => {
      const heading = [...document.querySelectorAll('h2')].find(h => h.textContent?.includes('Pipeline này làm gì'));
      const tabs = document.querySelector('nav.tabs');
      const approve = [...document.querySelectorAll('button')].find(b => b.textContent?.includes('Phê duyệt'));
      const follows = (el: Element | null | undefined) => !!heading && !!el && !!(heading.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
      return { tabs: follows(tabs), approve: follows(approve) };
    });
    expect(order).toEqual({ tabs: true, approve: true });

    // Tên dài phải xuống dòng trong khối, không tràn ngang.
    const box = await panel.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width);
    expect(await panel.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);

    await panel.screenshot({ path: testInfo.outputPath(`run-summary-${viewport.width}.png`) });
    await page.screenshot({ path: testInfo.outputPath(`run-summary-${viewport.width}-page.png`), fullPage: true });
  });
}

test('màn duyệt: thiếu dữ liệu thì chỉ hiện câu có căn cứ', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.goto('/runs/summary2?tab=overview');

  const panel = summaryPanel(page);
  await expect(panel.locator('li')).toHaveText(['Đọc 1 bảng: customers', 'Ra bảng fct_new_customers_monthly']);
  await expect(panel).not.toContainText('Chạy theo lịch');
  await expect(panel).not.toContainText('Chi phí');
  await expect(panel).not.toContainText('Optimizer');
  await panel.screenshot({ path: testInfo.outputPath('run-summary-missing-390.png') });
});
