import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  applyTransform, boundsOf, decodePathBlob, geometryToPath, toPathData,
} from '../src/fig-file/paths.mjs';

function encode(...cmds) {
  const parts = [];
  for (const [tag, ...args] of cmds) {
    const b = Buffer.alloc(1 + args.length * 4);
    b.writeUInt8(tag, 0);
    args.forEach((v, i) => b.writeFloatLE(v, 1 + i * 4));
    parts.push(b);
  }
  return Buffer.concat(parts);
}

const MOVE = 1, LINE = 2, QUAD = 3, CUBIC = 4, CLOSE = 0;

test('decodePathBlob reads each command with its documented arity', () => {
  const blob = encode(
    [MOVE, 1, 2],
    [LINE, 3, 4],
    [QUAD, 5, 6, 7, 8],
    [CUBIC, 9, 10, 11, 12, 13, 14],
    [CLOSE],
  );
  assert.equal(toPathData(decodePathBlob(blob)), 'M1 2 L3 4 Q5 6 7 8 C9 10 11 12 13 14 Z');
});
test('a wrong arity does not silently parse — the encoding is pinned', () => {
  // A CUBIC's 6 floats must not be readable as anything else. If the arity
  // table drifts, this stream stops landing on the trailing CLOSE tag and the
  // decode either throws or yields different commands. This is the guard that
  // makes the "consumes every real blob exactly" property meaningful.
  const blob = encode([CUBIC, 1, 2, 3, 4, 5, 6], [CLOSE]);
  const cmds = decodePathBlob(blob);
  assert.equal(cmds.length, 2);
  assert.deepEqual(cmds[0], { op: 'C', coords: [1, 2, 3, 4, 5, 6] });
  assert.deepEqual(cmds[1], { op: 'Z', coords: [] });
});

test('an unknown command tag throws instead of truncating', () => {
  // Truncating would emit a subtly-wrong shape, which is far harder to notice
  // than a raised diagnostic.
  assert.throws(() => decodePathBlob(Buffer.from([0x7f, 0, 0, 0, 0])), /unknown path command tag 127/);
});

test('a truncated command throws instead of reading past the end', () => {
  const short = Buffer.concat([Buffer.from([MOVE]), Buffer.alloc(4)]); // one float short
  assert.throws(() => decodePathBlob(short), /truncated/);
});

test('applyTransform bakes a mirror into the geometry', () => {
  const cmds = decodePathBlob(encode([MOVE, 0, 0], [LINE, 10, 0]));
  const flipped = applyTransform(cmds, { m00: -1, m01: 0, m02: 100, m10: 0, m11: 1, m12: 5 });
  assert.deepEqual(flipped[0].coords, [100, 5]);
  assert.deepEqual(flipped[1].coords, [90, 5]);
});

test('applyTransform maps Bézier control points like on-curve points', () => {
  const cmds = decodePathBlob(encode([CUBIC, 1, 1, 2, 2, 3, 3]));
  const t = applyTransform(cmds, { m00: 2, m01: 0, m02: 0, m10: 0, m11: 2, m12: 0 });
  assert.deepEqual(t[0].coords, [2, 2, 4, 4, 6, 6]);
});

test('boundsOf spans every point, control points included', () => {
  const b = boundsOf(decodePathBlob(encode([MOVE, 0, 0], [CUBIC, -5, 0, 20, 0, 10, 3])));
  assert.deepEqual([b.minX, b.minY, b.maxX, b.maxY], [-5, 0, 20, 3]);
});

// ── geometryToPath ──────────────────────────────────────────────────────────

const strokeOnlyNode = () => ({
  type: 'VECTOR',
  name: 'line',
  transform: { m00: 1, m01: 0, m02: 40, m10: 0, m11: 1, m12: 7 },
  // Figma emits a fillGeometry even for a stroke-only shape (an open path has a
  // notional fill region); picking it would paint a filled blob where the design
  // shows a hairline.
  fillGeometry: [{ commandsBlob: 0 }],
  strokeGeometry: [{ commandsBlob: 1 }],
  fillPaints: [],
  strokePaints: [{ type: 'SOLID', color: { r: 1, g: 0, b: 0, a: 1 }, visible: true }],
});

const blobs = [
  { bytes: encode([MOVE, 0, 0], [LINE, 0, 20], [CLOSE]) },              // 0: degenerate fill
  { bytes: encode([MOVE, -1, 0], [LINE, 1, 0], [LINE, 1, 20], [LINE, -1, 20], [CLOSE]) }, // 1: stroke outline
];

test('a stroke-only vector resolves to its stroke outline, painted as a fill', () => {
  // The stroke outline is already an expanded, fillable region. Re-stroking it
  // would outline the outline; painting it with the stroke color is correct.
  const r = geometryToPath(strokeOnlyNode(), blobs);
  assert.equal(r.paint, 'stroke');
  assert.equal(r.box.width, 2);
  assert.equal(r.box.height, 20);
  // Placed in parent space by the node transform, then normalized to a 0-origin
  // viewBox. A minX of -1 records the half-stroke overhang.
  assert.equal(r.box.minX, 39);
  assert.equal(r.box.minY, 7);
  assert.equal(r.d, 'M0 0 L2 0 L2 20 L0 20 Z');
});

test('serializes an outline with leading closes as valid SVG path data', () => {
  const blob = encode([CLOSE], [CLOSE], [MOVE, 1, 2], [LINE, 3, 4], [CLOSE]);
  assert.equal(toPathData(decodePathBlob(blob)), 'M1 2 L3 4 Z');
  assert.equal(toPathData(decodePathBlob(encode([CLOSE]))), '');
});
