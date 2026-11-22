import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { deflateRawSync } from 'node:zlib';
import { ByteReader, readSchema, makeDecoder } from '../src/fig-file/kiwi.mjs';
import { unzip, unpackFig, isZip, decompressChunk } from '../src/fig-file/container.mjs';
import {
  buildScene, outline, findFrame, materializeFrame, countFramesByName, framesByName, nodesByName,
} from '../src/fig-file/scene.mjs';
import { makeSyntheticFig } from './support/make_synthetic_fig.mjs';

const FIXTURE = new URL('./fixtures/synthetic.fig', import.meta.url);

function loadFixtureScene() {
  const container = unpackFig(readFileSync(FIXTURE));
  const schema = readSchema(new ByteReader(container.schemaBytes));
  const decoder = makeDecoder(schema);
  const message = decoder.decodeMessage(new ByteReader(container.messageBytes), decoder.rootIndex);
  return { scene: buildScene(message), container, schema, decoder };
}

test('ByteReader round-trips varint, float, and string encodings', () => {
  // Encode a few values with the generator's writer path by exercising the
  // reader against known kiwi byte sequences.
  const r = new ByteReader(Buffer.from([0x00])); // float zero optimization
  assert.equal(r.float(), 0);

  const s = new ByteReader(Buffer.from([0x96, 0x01])); // varuint 150
  assert.equal(s.varUint(), 150);

  const str = new ByteReader(Buffer.from([0x68, 0x69, 0x00, 0xff])); // "hi\0"
  assert.equal(str.string(), 'hi');
  assert.equal(str.byte(), 0xff);
});

test('fixture is a ZIP container with an inner canvas', () => {
  const bytes = readFileSync(FIXTURE);
  assert.ok(isZip(bytes), 'fixture should be a ZIP');
  const container = unpackFig(bytes);
  assert.equal(container.magic, 'fig-kiwi');
  assert.equal(container.version, 106);
  assert.ok(container.images.size >= 1, 'should bundle at least one image');
  assert.equal(container.meta && container.meta.file_name, 'Synthetic Fixture');
});

test('schema decodes and exposes a root Message type', () => {
  const { schema, decoder } = loadFixtureScene();
  assert.ok(schema.length >= 5, 'schema should carry several definitions');
  assert.notEqual(decoder.rootIndex, undefined, 'root Message must resolve');
});

test('outline lists pages and their top-level frames', () => {
  const { scene, container } = loadFixtureScene();
  const o = outline(scene, container.meta);
  assert.equal(o.pageCount, 2);
  const pageOne = o.pages.find((p) => p.name === 'Page One');
  assert.ok(pageOne, 'Page One present');
  // 'Plugin UI' plus the 'Paint Lab' paint-stack fixture frame.
  assert.equal(pageOne.frameCount, 2);
  assert.equal(pageOne.frames[0].name, 'Plugin UI');
  assert.equal(pageOne.frames[0].width, 320);
  assert.equal(pageOne.frames[0].height, 200);
  const empty = o.pages.find((p) => p.name === 'Empty Page');
  assert.equal(empty.frameCount, 0);
});

test('findFrame resolves by name and by guid', () => {
  const { scene } = loadFixtureScene();
  assert.equal(findFrame(scene, 'Plugin UI').name, 'Plugin UI');
  assert.equal(findFrame(scene, '0:2').name, 'Plugin UI');
  assert.equal(findFrame(scene, 'plugin ui').name, 'Plugin UI', 'case-insensitive');
  assert.equal(findFrame(scene, 'nope'), null);
});

// A name shared by two top-level frames on separate pages is ambiguous.
function twoPageDuplicateScene() {
  const frame = (local, name, parentLocal) => ({
    guid: { sessionID: 0, localID: local }, type: 'FRAME', name,
    parentIndex: { guid: { sessionID: 0, localID: parentLocal }, position: 'a' },
    size: { x: 10, y: 10 },
  });
  return buildScene({ nodeChanges: [
    { guid: { sessionID: 0, localID: 1 }, type: 'CANVAS', name: 'Page One' },
    { guid: { sessionID: 0, localID: 2 }, type: 'CANVAS', name: 'Page Two' },
    frame(3, 'Panel', 1),   // Page One / Panel
    frame(4, 'Panel', 2),   // Page Two / Panel  (same name, different page)
    frame(5, 'Solo', 1),    // Page One / Solo   (unique)
  ]});
}

test('framesByName reports every match with its page and guid', () => {
  const scene = twoPageDuplicateScene();
  const matches = framesByName(scene, 'Panel');
  assert.equal(matches.length, 2);
  assert.deepEqual(matches.map((m) => m.page).sort(), ['Page One', 'Page Two']);
  assert.equal(countFramesByName(scene, 'Panel'), 2);        // delegates to framesByName
  assert.equal(countFramesByName(scene, 'Solo'), 1);
  // A page restriction narrows the match set.
  const onPageTwo = framesByName(scene, 'Panel', 'Page Two');
  assert.equal(onPageTwo.length, 1);
  assert.equal(onPageTwo[0].page, 'Page Two');
});

test('findFrame restricts a name lookup to the requested page', () => {
  const scene = twoPageDuplicateScene();
  const onPageOne = findFrame(scene, 'Panel', 'Page One');
  const onPageTwo = findFrame(scene, 'Panel', 'Page Two');
  assert.ok(onPageOne && onPageTwo);
  // Each page-scoped search selects a distinct frame.
  assert.equal(onPageOne.guid.localID, 3);
  assert.equal(onPageTwo.guid.localID, 4);
  // A name absent from the requested page is not found, even if it exists elsewhere.
  assert.equal(findFrame(scene, 'Solo', 'Page Two'), null);
  // A guid is global; a page hint does not suppress it.
  assert.equal(findFrame(scene, '0:4', 'Page One').guid.localID, 4);
});

test('a colon in a frame name is not mistaken for a guid', () => {
  // Two top-level frames literally named "16:9". A substring `:` test
  // would wrongly treat as a guid, skipping the ambiguity guard. No node has the
  // guid "16:9", so guid-by-membership correctly classifies it as a name.
  const frame = (local, name, parentLocal) => ({
    guid: { sessionID: 0, localID: local }, type: 'FRAME', name,
    parentIndex: { guid: { sessionID: 0, localID: parentLocal }, position: 'a' },
    size: { x: 10, y: 10 },
  });
  const scene = buildScene({ nodeChanges: [
    { guid: { sessionID: 0, localID: 1 }, type: 'CANVAS', name: 'Page One' },
    { guid: { sessionID: 0, localID: 2 }, type: 'CANVAS', name: 'Page Two' },
    frame(3, '16:9', 1),
    frame(4, '16:9', 2),
  ]});
  assert.equal(scene.byGuid.has('16:9'), false, 'the name is not a guid key');
  assert.equal(framesByName(scene, '16:9').length, 2, 'both same-named frames are seen');
});

test('nodesByName finds same-named nested nodes for the fallback guard', () => {
  // Two nodes named "Button" nested under different top-level frames, with no
  // top-level "Button", so findFrame falls back to a nested match.
  const scene = buildScene({ nodeChanges: [
    { guid: { sessionID: 0, localID: 1 }, type: 'CANVAS', name: 'Page' },
    { guid: { sessionID: 0, localID: 2 }, type: 'FRAME', name: 'Card A',
      parentIndex: { guid: { sessionID: 0, localID: 1 }, position: 'a' }, size: { x: 10, y: 10 } },
    { guid: { sessionID: 0, localID: 3 }, type: 'FRAME', name: 'Card B',
      parentIndex: { guid: { sessionID: 0, localID: 1 }, position: 'b' }, size: { x: 10, y: 10 } },
    { guid: { sessionID: 0, localID: 4 }, type: 'FRAME', name: 'Button',
      parentIndex: { guid: { sessionID: 0, localID: 2 }, position: 'a' }, size: { x: 5, y: 5 } },
    { guid: { sessionID: 0, localID: 5 }, type: 'FRAME', name: 'Button',
      parentIndex: { guid: { sessionID: 0, localID: 3 }, position: 'a' }, size: { x: 5, y: 5 } },
  ]});
  assert.equal(framesByName(scene, 'Button').length, 0, 'no top-level Button');
  assert.equal(nodesByName(scene, 'Button').length, 2, 'two nested Buttons caught');
});

test('materializeFrame builds a valid figma-plugin envelope', () => {
  const { scene, container } = loadFixtureScene();
  const frame = findFrame(scene, 'Plugin UI');
  const { envelope } = materializeFrame(scene, frame, {
    images: container.images,
    fileKey: 'TESTKEY',
    parserVersion: '0.1.0-test',
    compatSchemaVersion: '1',
    exportedAt: '1970-01-01T00:00:00Z',
  });
  assert.equal(envelope.format_version, '2026.05-figma-plugin-v1');
  assert.equal(envelope.provenance.adapter, 'figma-plugin');
  assert.equal(envelope.root.name, 'Plugin UI');
  assert.equal(envelope.root.style.width, 320);
  assert.equal(envelope.root.style.background_color, '#14171c');
  assert.equal(envelope.root.style.border_radius, 8);

  const knob = envelope.root.children.find((c) => c.name.startsWith('Knob'));
  assert.ok(!('audio_widget' in knob), 'decoder does not classify widgets');
  assert.ok(knob.asset_ref, 'knob carries its image asset ref');
  assert.equal(knob.style.border_radius, 32);

  const label = envelope.root.children.find((c) => c.name === 'Label');
  assert.equal(label.type, 'text');
  assert.equal(label.content, 'CUTOFF');
  assert.ok(!('text' in label), 'must not emit the unread `text` key');
  assert.equal(label.style.color, '#ffffff');
  assert.ok(!('background_color' in label.style), 'text has no background');

  assert.equal(envelope.asset_manifest.assets.length, 1);
});

test('paint-stack lowering: opacity, scale mode, and honest diagnostics (Paint Lab)', () => {
  // The fixture generator writes the paint field IDs used by Figma exports.
  const { scene, container } = loadFixtureScene();
  const frame = findFrame(scene, 'Paint Lab');
  const { envelope, diagnostics } = materializeFrame(scene, frame, {
    images: container.images,
    fileKey: 'TESTKEY',
    parserVersion: '0.1.0-test',
    compatSchemaVersion: '1',
    exportedAt: '1970-01-01T00:00:00Z',
  });
  const child = (name) => envelope.root.children.find((c) => c.name === name);

  // Two solids composite source-over, bottom→top: #4b4d51 + white@0.55 →
  // #aeafb1 (the value Figma's own raster samples for this pair).
  assert.equal(child('Composite Chip').style.background_color, '#aeafb1');

  // FIT scale mode → object-fit contain (honored by ImageView::paint), and the
  // paint's 50% opacity folds into the childless node's layer opacity.
  const fit = child('Fit Photo');
  assert.ok(fit.asset_ref, 'image fill resolves to an asset');
  assert.equal(fit.style.object_fit, 'contain');
  assert.equal(fit.style.opacity, 0.5);

  // A VIDEO paint over a solid: the solid still lowers, and the unsupported
  // family is stated instead of silently dropped.
  assert.equal(child('Video Chip').style.background_color, '#0000ff');
  assert.ok(diagnostics.some((d) =>
    d.code === 'unsupported-paint-type' && d.node_name === 'Video Chip' && /VIDEO/.test(d.detail)));

  // TILE has no painter in the image widget: no object-fit keyword (stretch
  // default stands), and the approximation is stated.
  const tile = child('Tile Chip');
  assert.ok(tile.asset_ref);
  assert.ok(!('object_fit' in tile.style));
  assert.ok(diagnostics.some((d) =>
    d.code === 'image-scale-approximated' && d.node_name === 'Tile Chip'));
});

test('paint-stack lowering: gradient over solid keeps both slots; extras are diagnosed', () => {
  // Inline scenes for the stack shapes the fixture does not carry: a linear
  // gradient stacked over a solid (both slots lower. The old code silently
  // dropped the gradient), and a second solid above a gradient (no slot →
  // multi-paint-flattened; an OPAQUE one instead trims the stack exactly).
  const mkScene = (fills) => buildScene({ nodeChanges: [
    { guid: { sessionID: 0, localID: 1 }, type: 'CANVAS', name: 'Page' },
    { guid: { sessionID: 0, localID: 2 }, type: 'FRAME', name: 'Root',
      parentIndex: { guid: { sessionID: 0, localID: 1 }, position: 'a' },
      size: { x: 100, y: 100 } },
    { guid: { sessionID: 0, localID: 3 }, type: 'ROUNDED_RECTANGLE', name: 'chip',
      parentIndex: { guid: { sessionID: 0, localID: 2 }, position: 'a' },
      size: { x: 50, y: 50 }, fillPaints: fills },
  ] });
  const materialize = (fills) => {
    const scene = mkScene(fills);
    return materializeFrame(scene, findFrame(scene, 'Root'), { images: new Map() });
  };
  const linear = {
    type: 'GRADIENT_LINEAR',
    transform: { m00: 0, m01: 1, m02: 0, m10: 1, m11: 0, m12: 0 },
    stops: [
      { position: 0, color: { r: 1, g: 1, b: 1, a: 1 } },
      { position: 1, color: { r: 0, g: 0, b: 0, a: 1 } },
    ],
  };

  // Solid below + gradient above → BOTH slots, no diagnostic.
  const both = materialize([
    { type: 'SOLID', color: { r: 0, g: 0, b: 0, a: 1 } },
    linear,
  ]);
  const chip = both.envelope.root.children[0];
  assert.equal(chip.style.background_color, '#000000');
  assert.match(chip.style.background_gradient || '', /^linear-gradient\(/);
  assert.ok(!both.diagnostics.some((d) => d.code === 'multi-paint-flattened'));

  // A SEMI-TRANSPARENT solid above the gradient has no slot → diagnosed.
  const extra = materialize([
    linear,
    { type: 'SOLID', color: { r: 1, g: 0, b: 0, a: 1 }, opacity: 0.5 },
  ]);
  assert.ok(extra.diagnostics.some((d) =>
    d.code === 'multi-paint-flattened' && d.node_name === 'chip' && /SOLID/.test(d.detail)));

  // An OPAQUE solid above the gradient hides it exactly: trim, no diagnostic.
  const trimmed = materialize([
    linear,
    { type: 'SOLID', color: { r: 0.5, g: 0.5, b: 0.5, a: 1 } },
  ]);
  const trimmedChip = trimmed.envelope.root.children[0];
  assert.equal(trimmedChip.style.background_color, '#808080');
  assert.ok(!('background_gradient' in trimmedChip.style));
  assert.ok(!trimmed.diagnostics.some((d) => d.code === 'multi-paint-flattened'));
});

test('unterminated string throws instead of hanging', () => {
  const r = new ByteReader(Buffer.from([0x68, 0x69])); // "hi" with no NUL, then EOF
  assert.throws(() => r.string(), /unterminated string/);
});

test('a cyclic parent graph does not recurse without bound', () => {
  // Two frames reference each other as parents.
  const message = { nodeChanges: [
    { guid: { sessionID: 0, localID: 1 }, type: 'CANVAS', name: 'P' },
    { guid: { sessionID: 0, localID: 2 }, type: 'FRAME', name: 'A',
      parentIndex: { guid: { sessionID: 0, localID: 3 }, position: 'a' }, size: { x: 10, y: 10 } },
    { guid: { sessionID: 0, localID: 3 }, type: 'FRAME', name: 'B',
      parentIndex: { guid: { sessionID: 0, localID: 2 }, position: 'a' }, size: { x: 10, y: 10 } },
  ]};
  const scene = buildScene(message);
  // outline (countDescendants) and materialize (walk) must both terminate.
  const o = outline(scene, null);
  assert.ok(Array.isArray(o.pages));
  const frameA = findFrame(scene, 'A');
  const ctx = { images: new Map(), parserVersion: 't', compatSchemaVersion: '1', exportedAt: 'x' };
  const { envelope } = materializeFrame(scene, frameA, ctx);
  assert.equal(envelope.root.name, 'A');
});

test('fill alpha composites color.a * paint.opacity', () => {
  const message = { nodeChanges: [
    { guid: { sessionID: 0, localID: 1 }, type: 'FRAME', name: 'Box', size: { x: 10, y: 10 },
      fillPaints: [{ type: 'SOLID', visible: true, opacity: 0.5, color: { r: 0, g: 0, b: 0, a: 0.5 } }] },
  ]};
  const scene = buildScene(message);
  const { envelope } = materializeFrame(scene, findFrame(scene, 'Box'), {
    images: new Map(), parserVersion: 't', compatSchemaVersion: '1', exportedAt: 'x',
  });
  // 0.5 * 0.5 = 0.25 → alpha channel round(0.25*255)=64=0x40.
  assert.ok(envelope.root.style.background_color.toLowerCase().endsWith('40'),
    `expected 25% alpha suffix, got ${envelope.root.style.background_color}`);
});

test('countFramesByName detects duplicate frame names', () => {
  const message = { nodeChanges: [
    { guid: { sessionID: 0, localID: 1 }, type: 'CANVAS', name: 'P' },
    { guid: { sessionID: 0, localID: 2 }, type: 'FRAME', name: 'Main',
      parentIndex: { guid: { sessionID: 0, localID: 1 }, position: 'a' } },
    { guid: { sessionID: 0, localID: 3 }, type: 'FRAME', name: 'Main',
      parentIndex: { guid: { sessionID: 0, localID: 1 }, position: 'b' } },
  ]};
  const scene = buildScene(message);
  assert.equal(countFramesByName(scene, 'Main'), 2);
  assert.equal(countFramesByName(scene, 'Other'), 0);
});

test('committed fixture decodes to the same input as the generator', () => {
  const generated = unpackFig(makeSyntheticFig());
  const committed = unpackFig(readFileSync(FIXTURE));
  assert.deepEqual(generated.schemaBytes, committed.schemaBytes);
  assert.deepEqual(generated.messageBytes, committed.messageBytes);
  assert.deepEqual(generated.images, committed.images);
});

function localZipEntry(name, data) {
  const raw = deflateRawSync(data);
  const header = Buffer.alloc(30);
  header.writeUInt32LE(0x04034b50, 0);
  header.writeUInt16LE(8, 8);
  header.writeUInt32LE(raw.length, 18);
  header.writeUInt32LE(data.length, 22);
  header.writeUInt16LE(Buffer.byteLength(name), 26);
  return Buffer.concat([header, Buffer.from(name), raw]);
}

test('unzip bounds the cumulative decompressed size across entries', () => {
  const zip = Buffer.concat([
    localZipEntry('a.bin', Buffer.alloc(600)),
    localZipEntry('b.bin', Buffer.alloc(600)),
  ]);
  assert.equal(Object.keys(unzip(zip, { remaining: 1200 })).length, 2);
  assert.throws(() => unzip(zip, { remaining: 1000 }), RangeError);
});

test('unzip charges overlapping stored central-directory records', () => {
  const payload = Buffer.from('stored payload');
  const local = Buffer.alloc(30 + payload.length);
  local.writeUInt32LE(0x04034b50, 0);
  local.writeUInt16LE(8, 6); // data descriptor: force central-directory parsing
  local.writeUInt32LE(payload.length, 18);
  local.writeUInt32LE(payload.length, 22);
  local.writeUInt16LE(1, 26);
  const name = Buffer.from('x');
  const localFile = Buffer.concat([local, name, payload]);
  const central = [];
  for (let i = 0; i < 20; i++) {
    const row = Buffer.alloc(47);
    row.writeUInt32LE(0x02014b50, 0);
    row.writeUInt32LE(payload.length, 20);
    row.writeUInt32LE(payload.length, 24);
    row.writeUInt16LE(1, 28);
    row.writeUInt32LE(0, 42);
    central.push(Buffer.concat([row, name]));
  }
  const centralBytes = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(20, 8);
  eocd.writeUInt16LE(20, 10);
  eocd.writeUInt32LE(centralBytes.length, 12);
  eocd.writeUInt32LE(localFile.length, 16);
  assert.throws(() => unzip(Buffer.concat([localFile, centralBytes, eocd]), { remaining: payload.length * 10 }), RangeError);
});

test('inner chunks draw on the same cumulative decompression budget', () => {
  const chunk = deflateRawSync(Buffer.alloc(600));
  const budget = { remaining: 1000 };
  assert.equal(decompressChunk(chunk, budget).length, 600);
  assert.throws(() => decompressChunk(chunk, budget), RangeError);
});
