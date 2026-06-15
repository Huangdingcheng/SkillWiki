import puppeteer from 'puppeteer'
import { resolve } from 'path'

const PDF_PATH = resolve('E:/NLP/skill wiki/figures/figure2_overview/skillwiki_fig_2.drawio.pdf')
const OUT_PATH = resolve('E:/NLP/skill wiki/figures/figure2_overview/skillwiki_fig_2.png')

const browser = await puppeteer.launch({ headless: 'new', args: ['--no-sandbox', '--disable-setuid-sandbox'] })
const page = await browser.newPage()
await page.setViewport({ width: 1600, height: 900, deviceScaleFactor: 2 })
const fileUrl = 'file:///' + PDF_PATH.replace(/\\/g, '/')
await page.goto(fileUrl, { waitUntil: 'networkidle0', timeout: 15000 })
await new Promise(r => setTimeout(r, 2000))
await page.screenshot({ path: OUT_PATH, fullPage: true })
await browser.close()
console.log('Saved:', OUT_PATH)
