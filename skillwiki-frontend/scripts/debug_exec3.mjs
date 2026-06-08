import puppeteer from 'puppeteer'
const b = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] })
const p = await b.newPage()
await p.setViewport({ width: 1280, height: 720 })
await p.evaluateOnNewDocument(() => {
  localStorage.setItem('skillwiki-app-store', JSON.stringify({ state: { lang: 'en', darkMode: false }, version: 0 }))
})
p.on('response', async r => {
  if (r.url().includes('/ingest/parse')) console.log('PARSE RESPONSE', r.status())
})
p.on('requestfailed', r => { if (r.url().includes('/ingest')) console.log('FAIL', r.url()) })

await p.goto('http://localhost:3000/ingest', { waitUntil: 'networkidle2', timeout: 30000 })
const tabs = await p.$$('.ant-tabs-tab')
for (const t of tabs) {
  const txt = await t.evaluate(e => e.textContent)
  if (txt && txt.includes('API Doc')) { await t.click(); break }
}
await new Promise(r => setTimeout(r, 800))

// Set textarea via execCommand
const content = `GET /v1/models\nDescription: List models.\nResponse: {"data": [{"id": "string"}], "has_more": false}`
await p.evaluate((text) => {
  const ta = document.querySelector('textarea')
  if (!ta) return
  ta.focus(); ta.select()
  document.execCommand('insertText', false, text)
}, content)
await new Promise(r => setTimeout(r, 500))

// Scroll parse button into viewport, get rect, then use page.mouse.click
const rect = await p.evaluate(() => {
  const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent && b.textContent.includes('Parse'))
  if (!btn) return null
  btn.scrollIntoView({ behavior: 'instant', block: 'center' })
  const r = btn.getBoundingClientRect()
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
})
console.log('btn rect after scroll:', rect)

if (rect && rect.y >= 0 && rect.y <= 720) {
  await p.mouse.click(rect.x, rect.y)
  console.log('mouse clicked')
} else {
  console.log('button still out of viewport, y =', rect?.y)
}

await new Promise(r => setTimeout(r, 12000))
const ready = await p.evaluate(() => ({
  ready: document.body.innerText.includes('Ready'),
  parsed: document.body.innerText.includes('Parsed'),
}))
console.log('result:', ready)
await b.close()
