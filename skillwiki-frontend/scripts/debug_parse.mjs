import puppeteer from 'puppeteer'

const b = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] })
const p = await b.newPage()
await p.setViewport({ width: 1280, height: 720 })
await p.evaluateOnNewDocument(() => {
  localStorage.setItem('skillwiki-app-store', JSON.stringify({ state: { lang: 'en', darkMode: false }, version: 0 }))
})

p.on('response', async r => {
  if (r.url().includes('/ingest')) {
    const status = r.status()
    let body = ''
    try { body = (await r.text()).substring(0, 150) } catch {}
    console.log('API', r.url(), status, body)
  }
})
p.on('requestfailed', r => console.log('FAIL', r.url(), r.failure()?.errorText))

await p.goto('http://localhost:3000/ingest', { waitUntil: 'networkidle2', timeout: 30000 })

const tabs = await p.$$('.ant-tabs-tab')
for (const t of tabs) {
  const txt = await t.evaluate(e => e.textContent)
  if (txt && txt.includes('API Doc')) { await t.click(); break }
}
await new Promise(r => setTimeout(r, 500))

const ta = await p.$('textarea')
await ta.click({ clickCount: 3 })
await ta.press('Backspace')
await ta.type('GET /v1/models\nDescription: List models.\nResponse: {"data": []}', { delay: 0 })
await new Promise(r => setTimeout(r, 400))

const parseBtn = await p.evaluateHandle(() => {
  const btns = Array.from(document.querySelectorAll('button'))
  return btns.find(b => b.textContent && b.textContent.includes('Parse for'))
})
if (parseBtn) {
  const el = parseBtn.asElement()
  if (el) {
    await el.scrollIntoView()
    await el.click()
    console.log('clicked via puppeteer element click')
  }
}
await new Promise(r => setTimeout(r, 10000))
const bodyText = await p.evaluate(() => document.body.innerText)
console.log('Has Ready:', bodyText.includes('Ready'))
console.log('Has Parsed:', bodyText.includes('Parsed'))
console.log('Body snippet:', bodyText.substring(0, 300))
await b.close()
