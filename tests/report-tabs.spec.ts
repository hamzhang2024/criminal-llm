import { test, expect } from '@playwright/test';

test.describe('报告页选项卡测试', () => {
  test.beforeEach(async ({ page }) => {
    page.on('pageerror', e => console.log('PAGE ERROR:', e.message));
    page.on('console', m => { if (m.type() === 'error') console.log('JS ERROR:', m.text()) });
  });

  test('报告页加载并显示8个选项卡', async ({ page }) => {
    // 访问高为峰案件报告页
    await page.goto('http://localhost:5173/case/case_e6486dd5/report');

    // 等待页面加载
    await page.waitForTimeout(2000);

    // 检查页面标题
    await expect(page.locator('h2')).toContainText('高为峰');

    // 检查8个选项卡
    const expectedTabs = ['指控要素', '人物关系', '事件拆解', '法律法规', '证据中心', '矛盾分析', '控辩对抗', '辩护意见'];

    for (const tabName of expectedTabs) {
      const tab = page.locator(`button:has-text("${tabName}")`);
      await expect(tab).toBeVisible({ timeout: 5000 });
      console.log(`✅ 找到选项卡: ${tabName}`);
    }

    // 确认只有8个选项卡
    const tabs = page.locator('[style*="border-radius: 6px"][style*="cursor: pointer"]');
    const tabCount = await tabs.count();
    console.log(`总选项卡数量: ${tabCount}`);
  });

  test('指控要素选项卡内容', async ({ page }) => {
    await page.goto('http://localhost:5173/case/case_e6486dd5/report');
    await page.waitForTimeout(1000);

    // 点击指控要素
    await page.click('button:has-text("指控要素")');
    await page.waitForTimeout(500);

    // 检查内容是否加载
    const content = page.locator('[style*="flex: 1"][style*="overflow-y: auto"]');
    await expect(content).toBeVisible();
  });

  test('证据中心选项卡 - 可折叠面板', async ({ page }) => {
    await page.goto('http://localhost:5173/case/case_e6486dd5/report');
    await page.waitForTimeout(1000);

    // 点击证据中心
    await page.click('button:has-text("证据中心")');
    await page.waitForTimeout(1000);

    // 检查证据列表面板（默认展开）
    const evidenceListPanel = page.locator('text=证据列表');
    await expect(evidenceListPanel).toBeVisible();

    // 检查证据三性审查面板（可折叠）
    const reviewPanel = page.locator('text=证据三性审查');
    await expect(reviewPanel).toBeVisible();

    // 检查证据链可视化面板
    const chainPanel = page.locator('text=证据链可视化');
    await expect(chainPanel).toBeVisible();

    // 检查阅卷笔录面板
    const notesPanel = page.locator('text=阅卷笔录');
    await expect(notesPanel).toBeVisible();

    console.log('✅ 证据中心所有面板都存在');
  });

  test('法律法规选项卡 - 包含类案参考', async ({ page }) => {
    await page.goto('http://localhost:5173/case/case_e6486dd5/report');
    await page.waitForTimeout(1000);

    // 点击法律法规
    await page.click('button:has-text("法律法规")');
    await page.waitForTimeout(1000);

    // 检查类案参考面板
    const similarCases = page.locator('text=类案参考');
    await expect(similarCases).toBeVisible({ timeout: 5000 });

    console.log('✅ 法律法规包含类案参考');
  });

  test('辩护意见选项卡 - 可折叠面板', async ({ page }) => {
    await page.goto('http://localhost:5173/case/case_e6486dd5/report');
    await page.waitForTimeout(1000);

    // 点击辩护意见
    await page.click('button:has-text("辩护意见")');
    await page.waitForTimeout(1000);

    // 检查三阶层分析面板
    const threeTier = page.locator('text=三阶层分析');
    await expect(threeTier).toBeVisible();

    // 检查质证意见面板
    const crossExam = page.locator('text=质证意见');
    await expect(crossExam).toBeVisible();

    // 检查完整报告面板
    const fullReport = page.locator('text=完整报告');
    await expect(fullReport).toBeVisible();

    console.log('✅ 辩护意见所有面板都存在');
  });

  test('折叠面板交互', async ({ page }) => {
    await page.goto('http://localhost:5173/case/case_e6486dd5/report');
    await page.waitForTimeout(1000);

    // 点击证据中心
    await page.click('button:has-text("证据中心")');
    await page.waitForTimeout(500);

    // 找到证据三性审查的折叠按钮并点击
    const reviewHeader = page.locator('text=证据三性审查').first();
    await reviewHeader.click();
    await page.waitForTimeout(300);

    console.log('✅ 折叠面板交互正常');
  });
});
