#!/usr/bin/env node
// Failure rate of `prose check --doc` over the markdown under one or more directories.
//
//   node tests/prose_rate.mjs <prose-script> <out.json> <dir> [dir...]
//
// <prose-script> is a bin/prose path (run through sh). Prints the failure rate, the
// wall time, and how often each row fails. out.json keeps the per-file verdicts so
// a before and an after run can be diffed. Last line: `RATE DONE`.
import { spawn } from 'node:child_process';
import { readdirSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const [prose, out, ...dirs] = process.argv.slice(2);
if (!prose || !out || !dirs.length) {
  console.error('usage: prose_rate.mjs <prose-script> <out.json> <dir> [dir...]');
  process.exit(2);
}

function walk(d, acc) {
  for (const n of readdirSync(d)) {
    if (n === 'node_modules' || n.startsWith('.')) continue;
    const p = join(d, n);
    const s = statSync(p);
    if (s.isDirectory()) walk(p, acc);
    else if (n.endsWith('.md')) acc.push(p);
  }
  return acc;
}

const files = dirs.flatMap((d) => walk(d, []));
const results = {};
let next = 0;
const t0 = Date.now();

function one(file) {
  return new Promise((resolve) => {
    const c = spawn('sh', [prose, 'check', '--doc', file], { stdio: ['ignore', 'pipe', 'pipe'] });
    let buf = '';
    c.stdout.on('data', (d) => (buf += d));
    c.stderr.on('data', (d) => (buf += d));
    c.on('close', () => {
      const ok = /PROSE OK/.test(buf);
      const rows = [...buf.matchAll(/^\s{2}(\S[^\n]*?)\s{2,}\S+\s+(?:<= \S+\s+)?FAIL$/gm)].map((m) => m[1].trim());
      const vale = (buf.match(/Minh\.\w+/g) || []).filter((v) => v !== 'Minh.Unverified');
      results[file] = { ok, rows, vale: [...new Set(vale)], ran: /PROSE (OK|FAIL)/.test(buf) };
      resolve();
    });
  });
}

async function worker() {
  while (next < files.length) await one(files[next++]);
}

await Promise.all(Array.from({ length: 6 }, worker));
writeFileSync(out, JSON.stringify(results, null, 1));
const all = Object.values(results);
const failed = all.filter((r) => !r.ok).length;
const broken = all.filter((r) => !r.ran).length;
const tally = {};
for (const r of all) for (const k of [...r.rows, ...r.vale]) tally[k] = (tally[k] || 0) + 1;
console.log(`files ${all.length}, failed ${failed} (${((100 * failed) / all.length).toFixed(1)}%), did not run ${broken}`);
console.log(`wall ${((Date.now() - t0) / 1000).toFixed(0)} s`);
for (const [k, v] of Object.entries(tally).sort((a, b) => b[1] - a[1])) console.log(`  ${String(v).padStart(4)}  ${k}`);
console.log('RATE DONE');
