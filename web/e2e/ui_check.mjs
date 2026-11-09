// Drives the built page in headless Chromium against the real API with the planner faked
// (scripts/fake_planner_api.py): plan the mixed plate, play the program back, change the plan, undo.
// Run: npm run e2e (builds first). One-time setup: npx playwright-core install chromium-headless-shell
// Exits 1 if any check fails or the page logs an error. E2E_PORT sets the API port (default 8765).
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

const root = fileURLToPath(new URL('../..', import.meta.url))
const port = process.env.E2E_PORT ?? '8765'
const base = `http://127.0.0.1:${port}/`
const part = `${root}fixtures/parts/mixed.step`

const up = () => fetch(`${base}materials`).then((r) => r.ok, () => false)
if (await up()) throw new Error(`Something is already serving ${base}; stop it or set E2E_PORT.`)
// Its own process group, so stopping it also stops the uvicorn that `uv run` starts.
const server = spawn('uv', ['run', 'uvicorn', '--app-dir', 'scripts', 'fake_planner_api:app', '--port', port],
  { cwd: root, detached: true, stdio: ['ignore', 'ignore', 'inherit'] })
const stopServer = () => { try { process.kill(-server.pid) } catch { /* already gone */ } }
process.on('exit', stopServer)
for (let waited = 0; !(await up()); waited += 250) {
  if (waited > 60_000 || server.exitCode !== null) throw new Error('The fake-planner API did not start.')
  await new Promise((r) => setTimeout(r, 250))
}

const problems = []
const ok = (cond, what) => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${what}`)
  if (!cond) problems.push(what)
}
// SwiftShader gives headless Chromium a software WebGL for the three.js view.
const browser = await chromium.launch({ args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'] })
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } })
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`console error: ${m.text()}`) })
  page.on('pageerror', (e) => problems.push(`page error: ${e.message}`))

  await page.goto(base)
  await page.waitForSelector('select option', { state: 'attached' }) // options in a closed select are never "visible"
  ok((await page.locator('select option').count()) > 0, 'materials load into the dropdown')
  await page.setInputFiles('input[accept=".step,.stp"]', part)
  await page.click('button[type=submit]')
  await page.waitForSelector('text=All 6 checks passed', { timeout: 60_000 })
  ok((await page.locator('table tbody tr').count()) === 4, 'the holes table lists the 4 holes')
  ok((await page.locator('ol.ops li').count()) >= 4, 'the plan lists its operations')
  ok(await page.locator('button:has-text("Download .nc")').isEnabled(), 'download is enabled')
  ok(/Estimated cycle time \d+:\d\d/.test(await page.locator('text=Estimated cycle time').innerText()), 'cycle time is shown')

  const before = await page.locator('.step-what').innerText()
  await page.waitForTimeout(2500)
  const after = await page.locator('.step-what').innerText()
  const time = await page.locator('.time').innerText()
  ok(before !== after && !time.startsWith('0:00 '), `playback starts by itself and advances (${before} -> ${after}, ${time})`)
  await page.click('summary:has-text("G-code")')
  ok((await page.locator('pre span.active').count()) === 1, 'the running G-code line is highlighted')
  ok((await page.locator('.step-line').innerText()).startsWith('N'), 'the step shows its G-code line')

  await page.click('button.chip:has-text("Peck every hole")')
  await page.click('button:has-text("Revise with Claude")')
  await page.waitForSelector('.revision button:has-text("Undo")', { timeout: 60_000 })
  const revision = await page.locator('.revision').innerText()
  ok(revision.includes('Pecked the deep holes'), "the revision shows Claude's summary")
  ok((await page.locator('.diff li.added').count()) > 0 && (await page.locator('.diff li.removed').count()) > 0,
    'the revision lists the operations added and removed')
  ok(/Cycle time \d+:\d\d → /.test(revision), 'the cycle time before and after is shown')
  await page.click('.revision button:has-text("Undo")')
  ok((await page.locator('.revision').count()) === 0, 'undo clears the revision')
} finally {
  await browser.close()
}

console.log(problems.length ? `\n${problems.length} problem(s):\n${problems.join('\n')}` : '\nAll UI checks passed.')
process.exit(problems.length ? 1 : 0)
