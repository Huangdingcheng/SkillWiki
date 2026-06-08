/**
 * Captures four paper figures from the running SkillWiki frontend.
 * Run: node scripts/capture_figures.mjs
 * Output: ../figures/figure3_system/fig3_{scene}.png  (1280×720 each)
 */
import puppeteer from 'puppeteer'
import { mkdirSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const BASE_URL = 'http://localhost:3000'
const OUT_DIR = resolve(__dirname, '../../figures/figure3_system')
const W = 1280
const H = 720

mkdirSync(OUT_DIR, { recursive: true })

async function sleep(ms) {
  return new Promise(r => setTimeout(r, ms))
}

const browser = await puppeteer.launch({
  headless: 'new',
  args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
})

async function newPage() {
  const page = await browser.newPage()
  await page.setViewport({ width: W, height: H, deviceScaleFactor: 2 })
  await page.evaluateOnNewDocument(() => {
    localStorage.setItem(
      'skillwiki-app-store',
      JSON.stringify({ state: { lang: 'en', darkMode: false }, version: 0 }),
    )
  })
  return page
}

// ──────────────────────────────────────────────────────────────
// 1. Knowledge Ingestion — API Doc tab + content + parse running
// ──────────────────────────────────────────────────────────────
{
  console.log('Capturing: knowledge_ingestion ...')
  const page = await newPage()
  await page.goto(`${BASE_URL}/ingest`, { waitUntil: 'networkidle2', timeout: 30000 })
  await page.waitForSelector('.ant-tabs-tab', { timeout: 10000 })
  await sleep(600)

  // Scroll to top
  await page.evaluate(() => window.scrollTo(0, 0))

  // Switch to "API Doc" tab
  const tabs = await page.$$('.ant-tabs-tab')
  for (const tab of tabs) {
    const text = await tab.evaluate(el => el.textContent)
    if (text && text.includes('API Doc')) {
      await tab.click()
      break
    }
  }
  await sleep(500)

  // Fill textarea with API doc content
  const apiDocContent = `GET /v1/models
Description: List all available language models and their metadata.
Headers: { "Authorization": "Bearer <api_key>" }
Response: {
  "data": [
    { "id": "string", "object": "model", "created": "integer", "owned_by": "string" }
  ],
  "has_more": false
}
Errors: 401 Unauthorized when API key is missing or invalid.`

  // Clear and type content using Puppeteer's type() to properly trigger React state
  const ta = await page.$('textarea')
  if (ta) {
    await ta.click({ clickCount: 3 })
    await ta.press('Backspace')
    await sleep(200)
    await ta.type(`GET /v1/models\nDescription: List all available language models and metadata.\nHeaders: { "Authorization": "Bearer <api_key>" }\nResponse: { "data": [{ "id": "string", "object": "model", "owned_by": "string" }], "has_more": false }\nErrors: 401 Unauthorized when API key is missing.`, { delay: 0 })
  }
  await sleep(400)

  // Scroll to top to show tabs + textarea filled
  await page.evaluate(() => window.scrollTo(0, 0))
  await sleep(200)

  // Click Parse button — scroll it into view first
  const parseClicked = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('button'))
    const parseBtn = all.find(b => b.textContent && b.textContent.includes('Parse'))
    if (!parseBtn) return false
    parseBtn.scrollIntoView({ behavior: 'instant', block: 'center' })
    parseBtn.click()
    return true
  })
  console.log(`  Parse button clicked: ${parseClicked}`)
  await sleep(500)
  // Scroll back to top after clicking
  await page.evaluate(() => window.scrollTo(0, 0))

  // Wait for parsed result panel to appear (badge "Ready" or unit count card)
  try {
    await page.waitForFunction(
      () => {
        const body = document.body.innerText
        return body.includes('Ready') || body.includes('Parsed Units') || body.includes('Unit count')
      },
      { timeout: 15000 },
    )
    console.log('  Parse result detected')
  } catch {
    console.warn('  Parse result not detected, using fixed delay')
    await sleep(4000)
  }

  await sleep(600)
  await page.evaluate(() => window.scrollTo(0, 0))
  await sleep(200)

  const outPath = resolve(OUT_DIR, 'fig3_knowledge_ingestion.png')
  await page.screenshot({ path: outPath, fullPage: false })
  console.log(`  Saved: ${outPath}`)
  await page.close()
}

// ──────────────────────────────────────────────────────────────
// 2. Skill Exploration — full nebula + real node via ?preselect=
// ──────────────────────────────────────────────────────────────
{
  console.log('Capturing: skill_exploration ...')
  const page = await newPage()
  const REAL_SKILL_ID = 'test_graph_review_output'

  await page.goto(`${BASE_URL}/graph?preselect=${REAL_SKILL_ID}`, { waitUntil: 'load', timeout: 30000 })
  await page.waitForFunction(
    () => document.querySelectorAll('.ant-card').length >= 2,
    { timeout: 30000, polling: 500 },
  )
  await sleep(1000)
  try {
    await page.waitForSelector('canvas', { timeout: 25000 })
  } catch {
    console.warn('  canvas not found, proceeding anyway')
  }

  // Wait for force layout + preselect effect to fire
  await sleep(8000)
  await page.evaluate(() => window.scrollTo(0, 0))
  await sleep(300)

  const nodeSelected = await page.evaluate(() => {
    const items = document.querySelectorAll('.ant-card .ant-descriptions .ant-descriptions-item')
    return items.length > 0
  })

  const outPath = resolve(OUT_DIR, 'fig3_skill_exploration.png')
  await page.screenshot({ path: outPath, fullPage: false })
  console.log(`  Saved: ${outPath} (node selected: ${nodeSelected})`)
  await page.close()
}

// ──────────────────────────────────────────────────────────────
// 3. Governance (Dashboard)
// ──────────────────────────────────────────────────────────────
{
  console.log('Capturing: governance ...')
  const page = await newPage()
  await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle2', timeout: 30000 })
  await page.waitForSelector('.ant-statistic', { timeout: 10000 })
  await sleep(1800)

  const outPath = resolve(OUT_DIR, 'fig3_governance.png')
  await page.screenshot({ path: outPath, fullPage: false })
  console.log(`  Saved: ${outPath}`)
  await page.close()
}

// ──────────────────────────────────────────────────────────────
// 4. Evolution
// ──────────────────────────────────────────────────────────────
{
  console.log('Capturing: evolution ...')
  const page = await newPage()
  await page.goto(`${BASE_URL}/evolution`, { waitUntil: 'networkidle2', timeout: 30000 })
  await page.waitForSelector('.ant-statistic', { timeout: 10000 })
  await sleep(2000)

  const outPath = resolve(OUT_DIR, 'fig3_evolution.png')
  await page.screenshot({ path: outPath, fullPage: false })
  console.log(`  Saved: ${outPath}`)
  await page.close()
}

await browser.close()
console.log('\nAll figures saved to:', OUT_DIR)
