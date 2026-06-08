import puppeteer from 'puppeteer'
const b = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox'] })
const p = await b.newPage()
await p.setViewport({ width: 1280, height: 720 })
await p.evaluateOnNewDocument(() => localStorage.setItem('skillwiki-app-store', JSON.stringify({ state: { lang: 'en', darkMode: false }, version: 0 })))

p.on('response', async r => {
  if (r.url().includes('/ingest/parse')) {
    console.log('PARSE RESPONSE', r.status())
  }
})

await p.goto('http://localhost:3000/ingest', { waitUntil: 'networkidle2', timeout: 30000 })
const tabs = await p.$$('.ant-tabs-tab')
for (const t of tabs) { const txt = await t.evaluate(e => e.textContent); if (txt && txt.includes('API Doc')) { await t.click(); break } }
await new Promise(r => setTimeout(r, 800))

// Use React fiber to set textarea value and trigger onChange
const textSet = await p.evaluate((val) => {
  const ta = document.querySelector('textarea')
  if (!ta) return false

  // Find React internal props
  const key = Object.keys(ta).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'))
  if (!key) {
    // Fallback: use nativeInputValueSetter + change event
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
    nativeSetter.call(ta, val)
    ta.dispatchEvent(new Event('change', { bubbles: true }))
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    return 'fallback'
  }

  // Walk fiber to find onChange
  let fiber = ta[key]
  while (fiber) {
    const props = fiber.memoizedProps || fiber.pendingProps
    if (props && props.onChange) {
      props.onChange({ target: { value: val }, currentTarget: { value: val } })
      return 'fiber'
    }
    fiber = fiber.return
  }
  return false
}, 'GET /v1/models\nDescription: List available language models.\nResponse: {"data": [{"id": "string", "object": "model"}], "has_more": false}\nErrors: 401 Unauthorized.')

console.log('textSet method:', textSet)
await new Promise(r => setTimeout(r, 500))

// Check textarea and content state
const check = await p.evaluate(() => {
  const ta = document.querySelector('textarea')
  const btns = Array.from(document.querySelectorAll('button'))
  const btn = btns.find(b => b.textContent && b.textContent.includes('Parse'))
  return { taValue: ta ? ta.value.substring(0, 30) : null, btnDisabled: btn ? btn.disabled : null }
})
console.log('check:', check)

// Click parse
const parseEl = await p.evaluateHandle(() => Array.from(document.querySelectorAll('button')).find(b => b.textContent && b.textContent.includes('Parse')))
const asEl = parseEl.asElement()
if (asEl) { await asEl.scrollIntoView(); await asEl.click(); console.log('clicked') }

await new Promise(r => setTimeout(r, 10000))
const ready = await p.evaluate(() => document.body.innerText.includes('Ready') || document.body.innerText.includes('Parsed Units'))
console.log('has result:', ready)
await b.close()
