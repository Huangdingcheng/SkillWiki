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

await p.goto('http://localhost:3000/ingest', { waitUntil: 'networkidle2', timeout: 30000 })
const tabs = await p.$$('.ant-tabs-tab')
for (const t of tabs) {
  const txt = await t.evaluate(e => e.textContent)
  if (txt && txt.includes('API Doc')) { await t.click(); break }
}
await new Promise(r => setTimeout(r, 800))

const content = `GET /v1/models\nDescription: List available language models and metadata.\nResponse: {"data": [{"id": "string", "object": "model"}], "has_more": false}\nErrors: 401 Unauthorized.`
await p.evaluate((text) => {
  const ta = document.querySelector('textarea')
  if (!ta) return
  ta.focus(); ta.select()
  document.execCommand('insertText', false, text)
}, content)
await new Promise(r => setTimeout(r, 500))

// Scroll parse button into view and click with element.click()
const clicked = await p.evaluate(() => {
  const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent && b.textContent.includes('Parse'))
  if (!btn) return false
  btn.scrollIntoView({ behavior: 'instant', block: 'nearest' })
  btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }))
  btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }))
  btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  return true
})
console.log('clicked:', clicked)

await new Promise(r => setTimeout(r, 12000))
const ready = await p.evaluate(() => ({
  ready: document.body.innerText.includes('Ready'),
  parsed: document.body.innerText.includes('Parsed'),
}))
console.log('result:', ready)
await b.close()
