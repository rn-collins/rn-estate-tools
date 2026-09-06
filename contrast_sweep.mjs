#!/usr/bin/env node
/**
 * contrast-sweep — run contrast_audit.js across many live builds and keep the results.
 *
 * The audit itself has always been the right instrument; the problem was
 * operating it. It is a browser script, so every site had to be opened by hand,
 * injected, and read back — about ten of forty got measured that way, and none
 * of the numbers were written down. A sweep you cannot re-run is not a baseline.
 *
 * This drives headless Chrome over the DevTools Protocol with no dependencies
 * (Node 22+ has a global WebSocket), injects the same audit file, and writes one
 * JSON report for the whole estate.
 *
 * Usage:
 *   node contrast_sweep.mjs --batch urls.txt --json contrast-report.json
 *   node contrast_sweep.mjs https://one-site.vercel.app
 *   ... --settle 2500     wait longer for client-rendered pages
 *
 * Exit status is the number of builds with at least one failure, capped at 250.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const AUDIT = new URL('./contrast_audit.js', import.meta.url);
let PORT = 9333 + Math.floor(Math.random() * 400);
let PORT2 = null, chrome2 = null;

const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf(n); return i === -1 ? d : args[i + 1]; };
const settle = Number(opt('--settle', 1400));
const outPath = opt('--json', null);
const batch = opt('--batch', null);
const urls = batch
  ? readFileSync(batch, 'utf-8').split('\n').map(s => s.trim()).filter(s => s && !s.startsWith('#'))
  // file:// too, so a fix can be measured before it is pushed
  : args.filter(a => /^(https?|file):\/\//.test(a));

if (!urls.length) { console.error('no URLs given'); process.exit(2); }
const auditSrc = readFileSync(AUDIT, 'utf-8');

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function http(path) {
  const res = await fetch(`http://127.0.0.1:${PORT2 || PORT}${path}`, { method: path.startsWith('/json/new') ? 'PUT' : 'GET' });
  const text = await res.text();
  try { return JSON.parse(text); } catch { return text; }
}

// A minimal CDP client. One socket per tab; ids are per-socket.
function connect(wsUrl) {
  const ws = new WebSocket(wsUrl);
  const pending = new Map();
  const waiters = [];
  let id = 0;
  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    } else if (msg.method) {
      for (let i = waiters.length - 1; i >= 0; i--) {
        if (waiters[i].method === msg.method) { waiters[i].resolve(msg.params); waiters.splice(i, 1); }
      }
    }
  });
  const ready = new Promise((res, rej) => {
    ws.addEventListener('open', res);
    ws.addEventListener('error', () => rej(new Error('cdp socket error')));
  });
  return {
    ready,
    send(method, params = {}) {
      const mid = ++id;
      return new Promise((resolve, reject) => {
        pending.set(mid, { resolve, reject });
        ws.send(JSON.stringify({ id: mid, method, params }));
        setTimeout(() => { if (pending.delete(mid)) reject(new Error(`${method} timed out`)); }, 45000);
      });
    },
    once(method, ms = 30000) {
      return new Promise((resolve) => {
        const w = { method, resolve };
        waiters.push(w);
        setTimeout(() => { const i = waiters.indexOf(w); if (i > -1) { waiters.splice(i, 1); resolve(null); } }, ms);
      });
    },
    close() { try { ws.close(); } catch {} },
  };
}

async function auditOne(url) {
  const tab = await http(`/json/new?${encodeURIComponent(url)}`);
  if (!tab || !tab.webSocketDebuggerUrl) return { url, error: 'could not open tab' };
  const cdp = connect(tab.webSocketDebuggerUrl);
  try {
    await cdp.ready;
    await cdp.send('Page.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride',
      { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
    const loaded = cdp.once('Page.loadEventFired', 30000);
    await cdp.send('Page.navigate', { url });
    await loaded;
    // Most of this estate renders its content client-side; measuring at the
    // load event alone would score an empty page as flawless.
    await sleep(settle);
    await cdp.send('Runtime.evaluate', { expression: auditSrc, returnByValue: false, awaitPromise: false });
    const got = await cdp.send('Runtime.evaluate', {
      expression: "document.getElementById('__audit__') ? document.getElementById('__audit__').textContent : ''",
      returnByValue: true,
    });
    const raw = got?.result?.value || '';
    if (!raw) return { url, error: 'audit produced no rows' };
    const rows = JSON.parse(raw);
    // clipText rows are not scored: their computed colour is not the painted
    // one. They are reported separately so a real problem is not hidden either.
    const fails = rows.filter(r => !r.pass && !r.stateGlyph && !r.emojiOnly && !r.fallbackOnly && !r.inactive);
    const review = rows.filter(r => r.clipText);   // now scored, reported for provenance
    const stateGlyphs = rows.filter(r => r.stateGlyph && !r.pass);
    return {
      url,
      nodes: rows.length,
      failures: fails.length,
      clipText: review.length,
      stateGlyphs: stateGlyphs.length,
      clipTextRows: review.map(r => ({ sel: r.sel, text: r.text, px: r.px })),
      // worst over SCORED rows only: reporting 1.00 because an intentionally
      // invisible checkbox tick exists tells you nothing about the page.
      worst: (() => { const sc = rows.filter(r => !r.stateGlyph && !r.emojiOnly && !r.fallbackOnly && !r.inactive);
        return sc.length ? Math.min(...sc.map(r => r.ratio)) : null; })(),
      failing: fails.map(r => ({
        sel: r.sel, text: r.text, ratio: r.ratio, fg: r.fg, fgEff: r.fgEff,
        bg: r.bg, px: r.px, weight: r.weight, large: r.large,
      })),
    };
  } catch (e) {
    return { url, error: String(e.message || e) };
  } finally {
    cdp.close();
    if (tab.id) await http(`/json/close/${tab.id}`).catch(() => {});
  }
}

const profile = mkdtempSync(join(tmpdir(), 'contrast-sweep-'));
const chrome = spawn(CHROME, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--mute-audio',
  '--no-first-run', '--no-default-browser-check',
  `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, 'about:blank',
], { stdio: ['ignore', 'ignore', 'pipe'] });

// Only ever stop the process this script started. A name-matched kill once took
// down the user's own browser mid-session.
const shutdown = () => {
  try { chrome.kill('SIGTERM'); } catch {}
  if (chrome2) { try { chrome2.kill('SIGTERM'); } catch {} }
  setTimeout(() => { try { chrome.kill('SIGKILL'); } catch {} }, 3000).unref();
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
};
process.on('exit', shutdown);
process.on('SIGINT', () => { shutdown(); process.exit(130); });

let up = false;
for (let i = 0; i < 100; i++) {
  await sleep(200);
  const v = await http('/json/version').catch(() => null);
  if (v && v.webSocketDebuggerUrl) { up = true; break; }
}
if (!up) {
  // The port is chosen at random and can collide, and a cold Chrome sometimes
  // needs longer than the first window allows. One retry on a fresh port beats
  // losing a 50-minute sweep to a transient bind failure.
  console.error('debugging port did not come up; retrying once on a new port');
  try { chrome.kill('SIGTERM'); } catch {}
  await sleep(1500);
  PORT2 = 9333 + Math.floor(Math.random() * 400);
  chrome2 = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--hide-scrollbars', '--mute-audio',
    '--no-first-run', '--no-default-browser-check',
    `--remote-debugging-port=${PORT2}`, `--user-data-dir=${profile}2`, 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });
  for (let i = 0; i < 150; i++) {
    await sleep(200);
    const v = await fetch(`http://127.0.0.1:${PORT2}/json/version`).then(r => r.json()).catch(() => null);
    if (v && v.webSocketDebuggerUrl) { up = true; break; }
  }
  if (!up) { console.error('chrome did not expose a debugging port after retry'); process.exit(2); }
}

const results = [];
for (const url of urls) {
  const r = await auditOne(url);
  results.push(r);
  const name = url.replace('https://', '').replace('.vercel.app', '').replace(/\/$/, '');
  if (r.error) console.log(`  !! ${name.padEnd(38)} ${r.error}`);
  else console.log(`  ${(r.failures ? 'FAIL' : 'ok  ')} ${name.padEnd(38)} ${String(r.failures).padStart(4)} / ${String(r.nodes).padEnd(5)} worst ${r.worst}${r.clipText ? `   (${r.clipText} gradient-text, scored)` : ''}`);
}

const bad = results.filter(r => r.failures > 0);
const errored = results.filter(r => r.error);
console.log(`\n${results.length} builds · ${bad.length} with failures · ${errored.length} unreadable`);
console.log(`${results.reduce((n, r) => n + (r.failures || 0), 0)} failing text nodes total`);
if (outPath) { writeFileSync(outPath, JSON.stringify(results, null, 1)); console.log(`report written to ${outPath}`); }
shutdown();
process.exit(Math.min(bad.length, 250));
