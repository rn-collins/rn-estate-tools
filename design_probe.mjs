#!/usr/bin/env node
/**
 * design-probe — what typeface does the browser actually paint?
 *
 * design_survey.py reads stylesheets, and it says so in its own header: it
 * flagged rn-portfolio as "no choice made" because its display face is Arial,
 * which the parser treats as a fallback, while the page renders Arial Black at
 * 153px against acid green — one of the most deliberate things in the estate.
 * It also grouped that build with fifteen retired-module tombstones because the
 * word "georgia" appears somewhere in its CSS.
 *
 * Reading CSS cannot settle this. This asks the browser: for the largest text
 * on the page, what is the resolved font, size and weight, and what is the
 * ground behind it. A signature is (display face, size band, paper) as
 * rendered, so a build that chose Arial on purpose reads as a choice and a
 * build that inherited system-ui reads as a default.
 *
 * Usage: node design_probe.mjs --batch urls.txt [--json out.json]
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9500 + Math.floor(Math.random() * 300);
const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf(n); return i === -1 ? d : args[i + 1]; };
const urls = opt('--batch', null)
  ? readFileSync(opt('--batch'), 'utf-8').split('\n').map(s => s.trim()).filter(Boolean)
  : args.filter(a => /^https?:\/\//.test(a));
const outPath = opt('--json', null);
const settle = Number(opt('--settle', 1500));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const http = async (p) => {
  const res = await fetch(`http://127.0.0.1:${PORT}${p}`, { method: p.startsWith('/json/new') ? 'PUT' : 'GET' });
  const t = await res.text(); try { return JSON.parse(t); } catch { return t; }
};

const EXPR = `(() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && parseFloat(s.opacity) !== 0; };
  let best = null, bestPx = 0;
  for (const e of document.querySelectorAll('h1,h2,h3,.hero h1,[class*=title],[class*=hero]')) {
    const t = e.textContent.trim(); if (!t || !vis(e)) continue;
    const s = getComputedStyle(e); const px = parseFloat(s.fontSize);
    if (px > bestPx) { bestPx = px; best = { text: t.slice(0, 46), font: s.fontFamily,
      px: Math.round(px), weight: s.fontWeight, spacing: s.letterSpacing, transform: s.textTransform }; }
  }
  const b = getComputedStyle(document.body);
  let ground = '', n = document.body;
  while (n) { const c = getComputedStyle(n).backgroundColor;
    if (c && c !== 'rgba(0, 0, 0, 0)') { ground = c; break; } n = n.parentElement; }
  return JSON.stringify({ display: best, body: b.fontFamily, ground });
})()`;

const profile = mkdtempSync(join(tmpdir(), 'design-probe-'));
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars','--no-first-run',
  `--remote-debugging-port=${PORT}`,`--user-data-dir=${profile}`,'about:blank'], { stdio:['ignore','ignore','pipe'] });
const shutdown = () => { try { chrome.kill('SIGTERM'); } catch {} try { rmSync(profile,{recursive:true,force:true}); } catch {} };
process.on('exit', shutdown);
for (let i=0;i<100;i++){ await sleep(200); const v=await http('/json/version').catch(()=>null); if(v&&v.webSocketDebuggerUrl) break; }

const first = f => String(f || '').split(',')[0].replace(/["']/g,'').trim().toLowerCase();
const results = [];
for (const url of urls) {
  const tab = await http(`/json/new?${encodeURIComponent(url)}`);
  let row = { url, error: 'no tab' };
  if (tab?.webSocketDebuggerUrl) {
    const ws = new WebSocket(tab.webSocketDebuggerUrl);
    let id = 0; const pend = new Map();
    ws.addEventListener('message', ev => { const m = JSON.parse(ev.data);
      if (m.id && pend.has(m.id)) { pend.get(m.id)(m.result); pend.delete(m.id); } });
    const send = (method, params={}) => new Promise(res => { const i=++id; pend.set(i,res);
      ws.send(JSON.stringify({id:i,method,params})); setTimeout(()=>{if(pend.delete(i))res(null);},30000); });
    try {
      await new Promise((r,j)=>{ws.addEventListener('open',r);ws.addEventListener('error',()=>j(new Error('ws')));});
      await send('Page.enable');
      await send('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
      await send('Page.navigate',{url});
      await sleep(settle);
      const r = await send('Runtime.evaluate',{expression:EXPR,returnByValue:true});
      const v = r?.result?.value ? JSON.parse(r.result.value) : null;
      row = v ? { url, ...v } : { url, error: 'no reading' };
    } catch (e) { row = { url, error: String(e.message||e) }; }
    try { ws.close(); } catch {}
    if (tab.id) await http(`/json/close/${tab.id}`).catch(()=>{});
  }
  results.push(row);
  const name = url.replace('https://','').replace('.vercel.app','').replace(/\/$/,'');
  if (row.error) console.log(`  !! ${name.padEnd(34)} ${row.error}`);
  else console.log(`  ${name.padEnd(34)} ${first(row.display?.font).padEnd(22)} ${String(row.display?.px||'').padStart(4)}px/${row.display?.weight}  ${row.ground}`);
}

const sig = r => `${first(r.display?.font)} | ${first(r.body)} | ${r.ground}`;
const groups = {};
for (const r of results) { if (r.error) continue; (groups[sig(r)] ||= []).push(r.url.replace('https://','').replace('.vercel.app','')); }
const shared = Object.entries(groups).filter(([,v]) => v.length > 1);
console.log(`\n${results.length} builds · ${Object.keys(groups).length} distinct rendered signatures`);
if (shared.length) {
  console.log('\nBUILDS THAT RENDER ALIKE (display face | body face | paper):');
  for (const [k, v] of shared.sort((a,b)=>b[1].length-a[1].length)) {
    console.log(`\n  ${v.length} :: ${k}`);
    for (const s of v.sort()) console.log(`      ${s}`);
  }
}
if (outPath) { writeFileSync(outPath, JSON.stringify(results, null, 1)); console.log(`\nreport written to ${outPath}`); }
shutdown(); process.exit(0);
