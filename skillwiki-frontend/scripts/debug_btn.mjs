import puppeteer from 'puppeteer'
const b = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] })
const p = await b.newPage()
await p.setViewport({ width: 1280, height: 720 })
await p.evaluateOnNewDocument(() => localStorage.setItem('skillwiki-app-store', JSON.stringify({ state: { lang: 'en', darkMode: false }, version: 0 })))
await p.goto('http://localhost:3000/ingest', { waitUntil: 'networkidle2', timeout: 30000 })
const tabs = await p.$$('.ant-tabs-tab')
for (const t of tabs) { const txt = await t.evaluate(e => e.textContent); if (txt && txt.includes('API Doc')) { await t.click(); break } }
await new Promise(r => setTimeout(r, 800))
const ta = await p.$('textarea')
await ta.click({ clickCount: 3 })
await ta.press('Backspace')
await ta.type('GET /v1/models', { delay: 5 })
await new Promise(r => setTimeout(r, 600))
const info = await p.evaluate(() => {
  const btns = Array.from(document.querySelectorAll('button'))
  const parse = btns.find(b => b.textContent && b.textContent.includes('Parse'))
  const ta = document.querySelector('textarea')
  return {
    parseFound: !!parse,
    parseDisabled: parse ? parse.disabled : null,
    parseText: parse ? parse.textContent.substring(0, 40) : null,
    taValue: ta ? ta.value.substring(0, 40) : null,
    taLength: ta ? ta.value.length : 0
  }
})
console.log(JSON.stringify(info, null, 2))
await b.close()
