import puppeteer from 'puppeteer'
const b = await puppeteer.launch({
  headless: 'new',
  args: ['--no-sandbox', '--enable-clipboard-sanitized-write'],
})
const p = await b.newPage()
await p.setViewport({ width: 1280, height: 720 })
await p.evaluateOnNewDocument(() => {
  localStorage.setItem('skillwiki-app-store', JSON.stringify({ state: { lang: 'en', darkMode: false }, version: 0 }))
})

p.on('response', async r => {
  if (r.url().includes('/ingest/parse')) console.log('PARSE RESPONSE', r.status())
})

await p.goto('http://localhost:3000/ingest', { waitUntil: 'networkidle2', timeout: 30000 })

// Click API Doc tab
const tabs = await p.$$('.ant-tabs-tab')
for (const t of tabs) {
  const txt = await t.evaluate(e => e.textContent)
  if (txt && txt.includes('API Doc')) { await t.click(); break }
}
await new Promise(r => setTimeout(r, 800))

// Focus textarea, select all, then use execCommand to paste
const content = `GET /v1/models\nDescription: List available language models and metadata.\nResponse: {"data": [{"id": "string", "object": "model", "owned_by": "string"}], "has_more": false}\nErrors: 401 Unauthorized when API key is missing.`

const result = await p.evaluate((text) => {
  const ta = document.querySelector('textarea')
  if (!ta) return 'no textarea'
  ta.focus()
  ta.select()

  // Try document.execCommand with insertText — this triggers React onChange
  const ok = document.execCommand('insertText', false, text)
  return ok ? 'execCommand ok' : 'execCommand failed'
}, content)
console.log('input result:', result)

await new Promise(r => setTimeout(r, 500))
const taVal = await p.evaluate(() => document.querySelector('textarea')?.value?.substring(0, 30))
console.log('textarea value:', taVal)

// Click parse button using puppeteer's .click() on the element handle
const allBtns = await p.$$('button')
let clicked = false
for (const btn of allBtns) {
  const txt = await btn.evaluate(el => el.textContent)
  if (txt && txt.includes('Parse')) {
    const box = await btn.boundingBox()
    if (box) {
      await p.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
      console.log('clicked parse button at', box.x, box.y)
      clicked = true
    }
    break
  }
}
if (!clicked) console.log('parse button not found in viewport')

await new Promise(r => setTimeout(r, 12000))
const ready = await p.evaluate(() => {
  const t = document.body.innerText
  return { ready: t.includes('Ready'), parsed: t.includes('Parsed Units'), snippet: t.substring(200, 400) }
})
console.log('result:', ready)
await b.close()
