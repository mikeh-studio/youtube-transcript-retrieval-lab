/* Opt-in real-service check: requires the documented 14-item synthetic catalog fixture. */
const { chromium } = require('@playwright/test');
const fs = require('node:fs');
(async () => {
  const total = Number(process.env.CATALOG_FIXTURE_TOTAL || 14);
  const base = process.env.CATALOG_FLOW_URL;
  if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base)) throw new Error('Set explicit loopback CATALOG_FLOW_URL');
  const browser = await chromium.launch(process.env.CHROME_EXECUTABLE ? {executablePath: process.env.CHROME_EXECUTABLE} : {});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  if (process.env.CATALOG_CLOCK_SKEW_MS) await page.addInitScript(skew => {
    const now = Date.now; Date.now = () => now() + skew;
  }, Number(process.env.CATALOG_CLOCK_SKEW_MS));
  const errors = [], methods = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('request', r => {if (r.url().includes('/v1/catalog/')) methods.push(r.method());});
  const expect = require('@playwright/test').expect;
  try {
    await page.goto(base + '/catalog.html');
    await expect(page.locator('#total')).toHaveText(String(total));
    await expect(page.locator('.card')).toHaveCount(12);
    await expect(page.locator('#page')).toHaveText(`1–12 of ${total}`);
    const response = await page.request.get(base + '/v1/catalog/items');
    expect(response.headers()['cache-control']).toContain('no-store');
    expect(JSON.stringify(await response.json())).not.toContain('PRIVATE_SENTINEL');
    const output = process.env.CATALOG_SCREENSHOT_DIR;
    if (output) {fs.mkdirSync(output, {recursive:true}); await page.screenshot({path:output+'/catalog-desktop.png',fullPage:true});}
    await page.getByRole('button', {name:'View details'}).first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.locator('#detail-content')).toContainText('preconditions met');
    await expect(page.locator('#detail-content')).toContainText('not checked');
    await expect(page.locator('#detail-content')).toContainText('youtube data api');
    await page.getByRole('button', {name:'Close video details'}).click();
    await page.locator('#next').click(); await expect(page.locator('.card')).toHaveCount(total - 12);
    await expect(page.locator('#page')).toHaveText(`13–${total} of ${total}`);
    await page.locator('#reset-filters').click();
    await page.locator('[name=language]').selectOption('ja');
    await page.getByRole('button',{name:'Apply filters'}).click();
    await expect(page.locator('#total')).toHaveText('7');
    await page.locator('[name=language]').selectOption('');
    await page.locator('[name=term]').selectOption('sports');
    await page.getByRole('button',{name:'Apply filters'}).click();
    await expect(page.locator('#total')).toHaveText('5');
    await page.locator('[name=qualification]').selectOption('hold');
    await page.getByRole('button',{name:'Apply filters'}).click();
    await expect(page.locator('#status')).toHaveText('No videos match these filters.');
    await page.locator('#reset-filters').click(); await expect(page.locator('#total')).toHaveText(String(total));
    await page.setViewportSize({width:390,height:844});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    if(output) await page.screenshot({path:output+'/catalog-mobile.png',fullPage:true});
    await page.getByRole('button',{name:'View details'}).first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    if(output) await page.screenshot({path:output+'/catalog-detail-mobile.png'});
    expect(errors).toEqual([]);expect(methods.every(m=>m==='GET')).toBe(true);
    console.log(JSON.stringify({result:'passed',checks:['real API proxy','pagination','language','ontology descendants','empty state','detail provenance','readiness','no-store','redaction','mobile overflow','GET only'],pageErrors:errors}));
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
