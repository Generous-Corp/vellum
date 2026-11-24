// Reproduce the evidence for the decoder resolution records against pinned source.
// Usage: node decoder-evidence.mjs <Pulp repository> <new scratch directory>
import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { deflateRawSync } from 'node:zlib';

const pin = 'b0ee92d21944d5c956603afd1108b575e50c1302';
const [repo, scratchArg] = process.argv.slice(2);
assert.ok(repo && scratchArg, 'pass a Pulp repository and a new scratch directory');
const scratch = resolve(scratchArg);
mkdirSync(scratch); // Refuse to overwrite an existing directory.
const git = (...args) => execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8' });
const source = (path) => git('show', `${pin}:${path}`);
const container = source('tools/import-design/fig/container.mjs');
const cli = source('tools/import-design/fig_decode.mjs');
const paths = source('tools/import-design/fig/paths.mjs');
const generatorPath = 'tools/import-design/fig/testdata/make_synthetic_fig.mjs';
const generator = source(generatorPath);
const count = (text, needle) => text.split('\n').filter((line) => line.includes(needle)).length;
assert.equal(count(container, "import { inflateRawSync, zstdDecompressSync } from 'node:zlib'"), 1);
assert.equal(count(paths, "op !== 'Z'"), 0);
assert.equal(count(paths, 'export function toPathData'), 1);
console.log('quoted searches: zlib import=1, leading-close guard=0, toPathData control=1');

// Scale only the total cap to 128 bytes so rejection tests need no huge buffers.
assert.equal(count(container, 'export const MAX_TOTAL_DECOMPRESSED_BYTES = 512 * 1024 * 1024;'), 1);
const scaled = container.replace('export const MAX_TOTAL_DECOMPRESSED_BYTES = 512 * 1024 * 1024;',
  'export const MAX_TOTAL_DECOMPRESSED_BYTES = 128;');
const load = (text) => import(`data:text/javascript;base64,${Buffer.from(text).toString('base64')}`);
function canvas(size) {
  const chunk = deflateRawSync(Buffer.alloc(size, 65));
  const header = Buffer.alloc(12);
  header.write('fig-kiwi');
  header.writeUInt32LE(106, 8);
  const length = Buffer.alloc(4);
  length.writeUInt32LE(chunk.length);
  return Buffer.concat([header, length, chunk, length, chunk]);
}
function storedZip(name, bytes) {
  const header = Buffer.alloc(30);
  header.writeUInt32LE(0x04034b50);
  header.writeUInt32LE(bytes.length, 18);
  header.writeUInt32LE(bytes.length, 22);
  header.writeUInt16LE(Buffer.byteLength(name), 26);
  return Buffer.concat([header, Buffer.from(name), bytes]);
}
function chunkBudget(module) {
  assert.equal(module.unpackFig(canvas(50)).messageBytes.length, 50);
  assert.throws(() => module.unpackFig(canvas(70)), RangeError);
}
function zipBudget(module) {
  assert.equal(module.unpackFig(storedZip('canvas.fig', canvas(40))).messageBytes.length, 40);
  assert.throws(() => module.unpackFig(storedZip('canvas.fig', canvas(60))), RangeError);
}
function storedBudget(module) {
  const zip = Buffer.concat([storedZip('a', Buffer.alloc(60)), storedZip('b', Buffer.alloc(60))]);
  assert.equal(Object.keys(module.unzip(zip, { remaining: 120 })).length, 2);
  assert.throws(() => module.unzip(zip, { remaining: 119 }), RangeError);
}
const original = await load(scaled);
chunkBudget(original);
zipBudget(original);
storedBudget(original);
console.log('PASS shared inner-chunk, ZIP/inner-chunk, and stored-entry boundaries');

async function killed(label, before, after, check) {
  assert.ok(scaled.includes(before), `${label}: mutation must apply`);
  const mutated = await load(scaled.replace(before, after));
  assert.throws(() => check(mutated), assert.AssertionError, `${label}: probe must reject mutation`);
  console.log(`KILLED ${label}`);
}
await killed('separate inner budget', 'decompressChunk(chunks[1], budget)',
  'decompressChunk(chunks[1], { remaining: MAX_TOTAL_DECOMPRESSED_BYTES })', chunkBudget);
await killed('separate ZIP budget', 'unzip(fileBytes, budget)',
  'unzip(fileBytes, { remaining: MAX_TOTAL_DECOMPRESSED_BYTES })', zipBudget);
await killed('uncharged stored entries', 'budget.remaining -= raw.length;', '', storedBudget);

// Run the actual CLI, including decoding and selection, on a generated file.
const files = git('ls-tree', '-r', '--name-only', pin, 'tools/import-design/fig',
  'tools/import-design/fig_decode.mjs', 'test/fixtures/imports/fig/synthetic.fig').trim().split('\n');
for (const file of files) {
  const destination = join(scratch, file);
  mkdirSync(dirname(destination), { recursive: true });
  writeFileSync(destination, execFileSync('git', ['-C', repo, 'show', `${pin}:${file}`]));
}
const run = (...args) => spawnSync(process.execPath, args, { encoding: 'utf8', env: { ...process.env, TMPDIR: scratch } });
const upstreamTests = run('--test', '--test-name-pattern=unzip|outline lists|framesByName|findFrame|colon in a frame|nodesByName',
  join(scratch, 'tools/import-design/fig/fig.test.mjs'));
assert.equal(upstreamTests.status, 0, upstreamTests.stdout + upstreamTests.stderr);
console.log(upstreamTests.stdout);
assert.equal(count(generator, "name: 'Paint Lab'"), 1);
writeFileSync(join(scratch, generatorPath), generator.replace("name: 'Paint Lab'", "name: 'Plugin UI'"));
const fixture = join(scratch, 'duplicate.fig');
const generated = run(join(scratch, generatorPath), fixture);
assert.equal(generated.status, 0, generated.stderr);
const cliPath = join(scratch, 'tools/import-design/fig_decode.mjs');
const emit = (frame) => run(cliPath, 'emit', fixture, '--frame', frame, '--out', join(scratch, 'emitted'));
const rejected = emit('Plugin UI');
assert.equal(rejected.status, 2, rejected.stderr);
assert.match(rejected.stderr, /is ambiguous/);
assert.equal(emit('0:2').status, 0, 'explicit guid must succeed');
const outline = run(cliPath, 'outline', fixture, '--json');
assert.equal(outline.status, 0, outline.stderr);
assert.equal(JSON.parse(outline.stdout).pages[0].frames.filter((f) => f.name === 'Plugin UI').length, 2);
assert.equal(count(cli, 'if (matches.length > 1)'), 1);
try {
  writeFileSync(cliPath, cli.replace('if (matches.length > 1)', 'if (false)'));
  assert.equal(emit('Plugin UI').status, 0, 'removing the guard must admit the ambiguous name');
} finally {
  writeFileSync(cliPath, cli);
}
console.log('PASS real CLI outline, ambiguous-name rejection, explicit-guid control; KILLED removed ambiguity guard');
console.log(`PASS pinned decoder evidence at ${pin}`);
