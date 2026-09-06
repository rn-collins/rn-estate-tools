#!/usr/bin/env node
/**
 * probe — evaluate an expression on a live page and print the result.
 *
 * Companion to contrast_sweep: the sweep says a node fails, this says why.
 * A checkmark measured at 1.00:1 may be a real invisible glyph or a decorative
 * icon the design deliberately hides, and the difference is only visible in the
 * computed style. Guessing wrong means "fixing" something that was correct.
 *
 * Usage: node probe.mjs <url> '<js expression>' [--settle 1800]
 *        node probe.mjs <url> --shot '<selector>' out.png [--pad 40]
 *
 * --shot clips a screenshot to one element. The in-app browser pane cannot
 * screenshot scrolled content reliably while it is hidden, and "is this text
 * actually visible?" is a question only a picture answers.
 */
import { readFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9800 + Math.floor(Math.random() * 300);
const [url, expr] = process.argv.slice(2);
const si = process.argv.indexOf('--settle');
const settle = si === -1 ? 1800 : Number(process.argv[si + 1]);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const http = async (p) => {
  const res = await fetch(`http://127.0.0.1:${PORT}${p}`, { method: p.startsWith('/json/new') ? 'PUT' : 'GET' });
  const t = await res.text(); try { return JSON.parse(t); } catch { return t; }
};
const profile = mkdtempSync(join(tmpdir(), 'probe-'));
const chrome = spawn(CHROME, ['--headless=new','--disable-gpu','--hide-scrollbars','--no-first-run',
  `--remote-debugging-port=${PORT}`,`--user-data-dir=${profile}`,'about:blank'], { stdio:['ignore','ignore','pipe'] });
const shutdown = () => { try { chrome.kill('SIGTERM'); } catch {} try { rmSync(profile,{recursive:true,force:true}); } catch {} };
process.on('exit', shutdown);
for (let i=0;i<100;i++){ await sleep(200); const v=await http('/json/version').catch(()=>null); if(v&&v.webSocketDebuggerUrl) break; }
const tab = await http(`/json/new?${encodeURIComponent(url)}`);
const ws = new WebSocket(tab.webSocketDebuggerUrl);
let id=0; const pend=new Map();
ws.addEventListener('message', ev => { const m=JSON.parse(ev.data);
  if(m.id&&pend.has(m.id)){ const {res}=pend.get(m.id); pend.delete(m.id); res(m.result); } });
const send=(method,params={})=>new Promise(res=>{ const i=++id; pend.set(i,{res}); ws.send(JSON.stringify({id:i,method,params})); });
await new Promise(r=>ws.addEventListener('open',r));
await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
await send('Page.navigate',{url});
await sleep(settle);
const shotIdx = process.argv.indexOf('--shot');
if (shotIdx > -1) {
  const sel = process.argv[shotIdx + 1];
  const outFile = process.argv[shotIdx + 2];
  const pi = process.argv.indexOf('--pad');
  const pad = pi === -1 ? 40 : Number(process.argv[pi + 1]);
  // Scroll-reveal builds hold sections at opacity:0 until an observer fires,
  // which often never happens headless. Reveal everything before framing.
  await send('Runtime.evaluate', { expression:
    `document.querySelectorAll('.reveal,[class*=reveal]').forEach(e=>{e.classList.add('in','visible','is-visible','active');e.style.opacity='1';e.style.transform='none';});'ok'` });
  await sleep(900);   // let the reveal transition land before framing
  const box = await send('Runtime.evaluate', { returnByValue: true, expression:
    `(()=>{const e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;
      e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();
      return JSON.stringify({x:r.left+scrollX,y:r.top+scrollY,w:r.width,h:r.height});})()` });
  const b = box?.result?.value ? JSON.parse(box.result.value) : null;
  if (!b) { console.log('selector not found'); shutdown(); process.exit(1); }
  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true,
    clip: { x: Math.max(0, b.x - pad), y: Math.max(0, b.y - pad),
            width: Math.min(1280, b.w + pad * 2), height: b.h + pad * 2, scale: 2 } });
  const { writeFileSync } = await import('node:fs');
  writeFileSync(outFile, Buffer.from(shot.data, 'base64'));
  console.log(`wrote ${outFile}`);
} else {
  const out = await send('Runtime.evaluate',{expression:`JSON.stringify((()=>{${expr}})(),null,1)`,returnByValue:true});
  console.log(out?.result?.value ?? JSON.stringify(out));
}
shutdown(); process.exit(0);
