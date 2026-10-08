// Fresh Element login and recovery; trusted hashes of existing imported events.
// Usage: node element.cjs /private/config.json [--check-preparation]
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
process.umask(0o077);
const assert = (condition) => { if (!condition) throw new Error('Invalid witness input'); };
let browser;
let activePage;
let evidenceDirectory;
let sampleIndex = 0;
let samplesVerified = 0;
let stage = 'preparation';
(async () => {
  const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const manifest = JSON.parse(fs.readFileSync(cfg.manifest, 'utf8'));
  assert(['password', 'mas'].includes(cfg.login_mode));
  assert(manifest.provenance && manifest.samples.length > 0);
  assert(manifest.user_id.startsWith('@'));
  assert(manifest.samples.every(s => s.room_id.startsWith('!') && s.event_id.startsWith('$') && /^[a-f0-9]{64}$/.test(s.body_sha256)));
  const allowed = new Set(['127.0.0.1', 'localhost', '[::1]', ...(cfg.allowed_lab_hosts || [])]);
  // Extra hosts are limited to the existing in-cluster dark lab; public hosts are forbidden.
  assert((cfg.allowed_lab_hosts || []).every(h => h.endsWith('.spindle-rehearsal.svc.cluster.local')));
  assert(allowed.has(new URL(cfg.web_url).hostname));
  assert(cfg.password_file && cfg.recovery_key_file && cfg.playwright_module && cfg.output);
  if (process.argv.includes('--check-preparation')) {
    console.log('configuration valid; no browser launched or server contacted');
    return;
  }
  const { chromium } = require(cfg.playwright_module);
  evidenceDirectory = cfg.output;
  fs.mkdirSync(cfg.output, { mode: 0o700 }); // A run must have a fresh evidence directory.
  browser = await chromium.launch({ args: ['--host-resolver-rules=MAP *.spindle-rehearsal.svc.cluster.local 127.0.0.1'] });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
  await ctx.route('**/*', route => {
    const url = new URL(route.request().url());
    return allowed.has(url.hostname) ? route.continue() : route.abort();
  });
  const page = await ctx.newPage();
  activePage = page;
  let fixtureSession;
  page.on('response', async response => {
    const url = new URL(response.url());
    if (!cfg.cursor_material || !url.pathname.endsWith('/login') || !response.ok()) return;
    try {
      const body = await response.json();
      if (body.user_id === manifest.user_id && body.access_token && body.device_id) fixtureSession = { base: url.origin, accessToken: body.access_token };
    } catch {}
  });
  const wait = 60000;
  stage = 'login';
  await page.goto(`${cfg.web_url}/#/login`, { waitUntil: 'load' });
  const continueButton = page.getByRole('button', { name: /^continue$/i });
  if (await continueButton.count()) await continueButton.first().click({ timeout: wait });
  const localpart = manifest.user_id.slice(1).split(':')[0];
  const password = fs.readFileSync(cfg.password_file, 'utf8').trimEnd();
  if (cfg.login_mode === 'mas') {
    await page.waitForURL(u => u.hostname.endsWith('.spindle-rehearsal.svc.cluster.local'), { timeout: wait });
    await page.locator('input[name=username]').fill(localpart, { timeout: wait });
    await page.locator('input[name=password]').fill(password);
    await page.locator('button[type=submit]').first().click();
    for (let i = 0; i < 3; i++) {
      await page.waitForLoadState('load');
      if (!new URL(page.url()).hostname.endsWith('.spindle-rehearsal.svc.cluster.local')) break;
      const button = page.locator('button[type=submit]').first();
      if (await button.count()) await button.click();
      await page.waitForTimeout(1500);
    }
    await page.waitForURL(u => u.origin === new URL(cfg.web_url).origin, { timeout: wait });
  } else {
    await page.locator('input[name=username], input[name=user], input[autocomplete=username]').first().fill(localpart, { timeout: wait });
    await page.locator('input[type=password]').fill(password);
    await page.getByRole('button', { name: /^sign in$/i }).click();
  }
  stage = 'recovery';
  await page.getByRole('button', { name: /use (a |your )?(recovery|security) key|verify with (recovery|security) key/i }).first().click({ timeout: wait });
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('textbox').first().fill(fs.readFileSync(cfg.recovery_key_file, 'utf8').trim(), { timeout: wait });
  await dialog.getByRole('button', { name: /continue/i }).click();
  // Element only offers Done after successful identity verification/recovery.
  await page.getByRole('button', { name: /^done$/i }).click({ timeout: wait });
  const results = [];
  stage = 'event_hash_verification';
  for (const sample of manifest.samples) {
    stage = 'event_navigation';
    await page.goto(`${cfg.web_url}/#/room/${sample.room_id}/${sample.event_id}`);
    stage = 'event_room_view_wait';
    await page.locator('.mx_RoomView_MessageList').waitFor({ timeout: wait });
    stage = 'event_hash_verification';
    const tile = page.locator(`[data-scroll-tokens*="${sample.event_id.replaceAll('"', '\\"')}"]`).first();
    const deadline = Date.now() + 90000;
    let outcome = 'missing';
    while (Date.now() < deadline) {
      if (await tile.count()) {
        const utd = await tile.locator('.mx_DecryptionFailureBody:not(.mx_ReplyChain *), .mx_UnknownBody:not(.mx_ReplyChain *)').count();
        const body = await tile.locator('.mx_EventTile_body').first().innerText().catch(() => null);
        const hash = body === null ? null : crypto.createHash('sha256').update(body).digest('hex');
        outcome = utd ? 'unable_to_decrypt' : hash === sample.body_sha256 ? 'verified' : 'hash_mismatch';
        if (outcome === 'verified') break;
      }
      await page.waitForTimeout(1000);
    }
    results.push({ room_id: sample.room_id, event_id: sample.event_id, outcome });
    sampleIndex++;
    if (outcome === 'verified') samplesVerified++;
  }
  const pass = results.every(r => r.outcome === 'verified');
  let predecessorCursors;
  if (cfg.cursor_material) {
    stage = 'predecessor_cursor_http';
    assert(fixtureSession);
    predecessorCursors = await require('./cursor.cjs')({ ...fixtureSession, materialFile: cfg.cursor_material });
    fs.writeFileSync(path.join(cfg.output, 'predecessor-cursors.json'), JSON.stringify(predecessorCursors, null, 2), { flag: 'wx', mode: 0o600 });
    fixtureSession = undefined;
  }
  fs.writeFileSync(path.join(cfg.output, 'element.json'), JSON.stringify({
    user_id: manifest.user_id, provenance: manifest.provenance,
    fresh_login: true, recovery_ui_completed: true, samples: results,
    predecessor_cursor_http_pass: predecessorCursors?.passed || false,
    history_decryption_pass: pass, full_cutover_gate_pass: false,
    limitation: 'Combine with token/device, public cross-signing continuity, and encrypted-event API preflight evidence.'
  }, null, 2), { flag: 'wx', mode: 0o600 });
  await browser.close();
  browser = null;
  console.log(pass ? 'All existing-history sample hashes verified' : 'Existing-history sample verification failed');
  process.exitCode = pass ? 0 : 1;
})().catch(async error => {
  // Browser exceptions can contain redirect URLs, HTML, and credentials: suppress details.
  console.error(`Element witness failed at ${stage} (${error.name}); no pass claimed`);
  if (activePage && evidenceDirectory) {
    const diagnostic = { stage, error_type: error.name, sample_index: sampleIndex, verified_samples: samplesVerified,
      url_sha256: crypto.createHash('sha256').update(activePage.url()).digest('hex'), selector_counts: {} };
    for (const selector of ['.mx_RoomView_MessageList', '.mx_EventTile', '.mx_Dialog', '.mx_DecryptionFailureBody', '.mx_SyncError']) {
      diagnostic.selector_counts[selector] = await activePage.locator(selector).count().catch(() => null);
    }
    fs.writeFileSync(path.join(evidenceDirectory, 'failure-diagnostic.json'), JSON.stringify(diagnostic, null, 2), { flag: 'wx', mode: 0o600 });
  }
  if (browser) await browser.close();
  process.exitCode = 2;
});
