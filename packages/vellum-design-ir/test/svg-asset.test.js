import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
    importSvgAsset,
    indexTree,
    SVG_ASSET_LIMITS,
    svgAssetToDesignIR,
    SvgAssetRefusal,
} from '../src/index.js';
import { svgAssetTesting } from '../src/svg-asset.js';

const fixtures = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'fixtures',
    'vector-assets');
const source = { key: 'assets', adapter: 'svg', adapterVersion: '1', formatVersion: '1.1',
    revision: 'test' };
const fixture = (name) => readFile(join(fixtures, `${name}.svg`), 'utf8');
const wrap = (body, attributes = '') =>
    `<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"${attributes}>${body}</svg>`;
const codes = (document) => document.lossReport.losses.map((loss) => loss.code).sort();

test('each fixture takes its preferred form and reports what it lost', async () => {
    const expected = {
        gradients: ['subtree', []],
        'clip-mask': ['subtree', ['svg-mask-region-unclipped']],
        use: ['subtree', []],
        'compiled-only': ['compiled', []],
        'over-budget': ['compiled', []],
    };
    for (const [name, [mode, losses]] of Object.entries(expected)) {
        const imported = svgAssetToDesignIR(await fixture(name), { source });
        assert.equal(imported.mode, mode, name);
        assert.deepEqual(codes(imported.document), losses, name);
        assert.equal(imported.document.lossReport.lossCount, losses.length, name);
    }
});

test('a compiled asset says why it was compiled', async () => {
    const compiled = importSvgAsset(await fixture('compiled-only'));
    const decision = compiled.diagnostics.find((entry) => entry.code === 'svg-compiled');
    assert.match(decision.message, /userSpaceOnUse gradient units/);
    assert.match(decision.message, /a gradientTransform/);
    assert.match(decision.message, /a radial gradient with a focal point/);
    const budget = importSvgAsset(await fixture('over-budget'));
    assert.match(budget.diagnostics.find((entry) => entry.code === 'svg-compiled').message,
        /522 nodes of 512/);
    assert.equal(budget.root.children.length, 1);
    assert.equal(budget.root.children[0].kind, 'vector');
    assert.equal(budget.root.children[0].properties.vector.asset.ops.length, 522);
});

test('the path-data budget compiles an asset under its node budget', async () => {
    const svg = await fixture('gradients');
    assert.equal(importSvgAsset(svg).mode, 'subtree');
    const tight = importSvgAsset(svg, { pathDataBudget: 40 });
    assert.equal(tight.mode, 'compiled');
    assert.match(tight.diagnostics.at(-1).message, /bytes of path data of 40/);
});

test('compiling a token-bound subtree reports the baked tokens as a loss', async () => {
    const imported = svgAssetToDesignIR(await fixture('gradients'), {
        source, nodeBudget: 4, colorTokens: { '#e11d48': 'assets.brand.accent' },
        tokens: { 'assets.brand.accent': { $type: 'color', $value: '#e11d48' } },
    });
    assert.equal(imported.mode, 'compiled');
    assert.deepEqual(codes(imported.document), ['svg-compiled-tokens-baked']);
});

test('token-bound colours and stable identities survive a subtree import', async () => {
    const svg = await fixture('gradients');
    const options = { source, colorTokens: { '#e11d48': 'assets.brand.accent' },
        tokens: { 'assets.brand.accent': { $type: 'color', $value: '#e11d48' } } };
    const first = svgAssetToDesignIR(svg, options).document;
    const second = svgAssetToDesignIR(svg, options).document;
    const ids = [...indexTree(first.root).index.keys()];
    assert.deepEqual(ids, [...indexTree(second.root).index.keys()]);
    const swatch = indexTree(first.root).index.get('assets/svg:swatch').node;
    assert.equal(swatch.properties.path.fill, '{assets.brand.accent}');
    const uses = svgAssetToDesignIR(await fixture('use'), { source }).document;
    const useIds = [...indexTree(uses.root).index.keys()];
    assert.equal(new Set(useIds).size, useIds.length);
    assert.ok(useIds.includes('assets/svg:first:tile.0'));
    assert.ok(useIds.includes('assets/svg:second:tile.0'));
    assert.ok(useIds.includes('assets/svg:pairs:pairing.1:tile.1'));
});

test('rasterization is the last fallback and is always a recorded loss', async () => {
    const svg = await fixture('raster-only');
    const dropped = svgAssetToDesignIR(svg, { source });
    assert.equal(dropped.mode, 'subtree');
    assert.deepEqual(codes(dropped.document),
        ['svg-raster-feature-dropped', 'svg-raster-feature-dropped']);
    let calls = 0;
    const rasterize = (input, size) => {
        calls += 1;
        assert.equal(input, svg);
        assert.deepEqual(size, { width: 120, height: 60 });
        return { contentHash: `sha256:${'b'.repeat(64)}`, mimeType: 'image/png' };
    };
    const raster = svgAssetToDesignIR(svg, { source, rasterize });
    assert.equal(calls, 1);
    assert.equal(raster.mode, 'rasterized');
    assert.deepEqual(codes(raster.document), ['svg-rasterized']);
    assert.equal(raster.document.lossReport.byDisposition.rasterized, 1);
    assert.match(raster.document.lossReport.losses[0].message, /filter needs rasterization/);
    assert.equal(raster.document.assets[0].contentHash, `sha256:${'b'.repeat(64)}`);
    const image = raster.document.root.children[0];
    assert.equal(image.kind, 'image');
    assert.equal(image.properties.image.source, `sha256:${'b'.repeat(64)}`);
    assert.equal(importSvgAsset(await fixture('gradients'), { rasterize }).mode, 'subtree');
    assert.equal(calls, 1, 'an asset the primitives express is never rasterized');
});

test('every dropped or approximated feature is a loss entry', () => {
    const svg = wrap(
        '<style>rect { fill: red }</style>' +
        '<rect width="5" height="5" shape-rendering="crispEdges"><animate attributeName="x" to="2"/></rect>' +
        '<rect width="5em" height="5" fill="hsl(0, 100%, 50%)"/>' +
        '<a href="#r"><rect id="r" width="1" height="1" fill="context-fill"/></a>' +
        '<use href="#missing"/>' +
        '<foo/>');
    const document = svgAssetToDesignIR(svg, { source }).document;
    assert.deepEqual(codes(document), [
        'svg-animation-dropped', 'svg-attribute-dropped', 'svg-element-dropped',
        'svg-length-unit-dropped', 'svg-link-dropped', 'svg-property-invalid',
        'svg-style-element-dropped', 'svg-use-unresolved',
    ]);
});

const hostile = {
    'a document type': '<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
    'an unknown entity': wrap('<title>&nbsp;</title>'),
    'a processing instruction': '<?xml-stylesheet href="http://example.invalid/x.css"?>' + wrap(''),
    'a script': wrap('<script>alert(1)</script>'),
    'an event handler': wrap('<rect width="1" height="1" onclick="alert(1)"/>'),
    'a foreign object': wrap('<foreignObject><div/></foreignObject>'),
    'an external use': wrap('<use href="http://example.invalid/sprite.svg#icon"/>'),
    'a data URL reference': wrap('<use xlink:href="data:image/svg+xml;base64,AAAA"/>'),
    'an external paint': wrap('<rect width="1" height="1" fill="url(http://example.invalid/#g)"/>'),
    'an external style URL': wrap('<rect width="1" height="1" style="fill: url(file:///etc/passwd)"/>'),
    'a style import': wrap('<style>@import "http://example.invalid/x.css";</style>'),
    'a use cycle': wrap('<g id="a"><use href="#a"/></g>'),
    'an indirect use cycle': wrap('<g id="a"><g id="b"><use href="#c"/></g></g><g id="c"><use href="#a"/></g>'),
    'a gradient cycle': wrap('<linearGradient id="p" href="#q"/><linearGradient id="q" href="#p"/>'),
    'a mask that masks itself': wrap('<mask id="m"><rect width="1" height="1" mask="url(#m)"/></mask>'),
    'a huge coordinate': wrap('<path d="M0 0L1e9 0"/>'),
    'an overflowing number': wrap('<rect width="1e999" height="1"/>'),
    'a huge transform': wrap('<g transform="scale(10000000)"/>'),
    'a huge style number': wrap('<rect width="1" height="1" style="stroke-width: 9e99"/>'),
    'duplicate ids': wrap('<rect id="x"/><rect id="x"/>'),
    'an unclosed element': '<svg xmlns="http://www.w3.org/2000/svg"><g>',
    'two roots': wrap('') + wrap(''),
    'a CDATA section': wrap('<style><![CDATA[rect{}]]></style>'),
};

for (const [name, svg] of Object.entries(hostile)) {
    test(`refuses ${name} before any work`, () => {
        let rasterized = false;
        assert.throws(() => importSvgAsset(svg, { rasterize: () => { rasterized = true; } }),
            SvgAssetRefusal);
        assert.equal(rasterized, false);
    });
}

test('refuses a use expansion bomb and oversized or deeply nested input', () => {
    let body = '<rect id="l0" width="1" height="1"/>';
    for (let level = 1; level <= 8; level += 1) {
        body += `<g id="l${level}">${`<use href="#l${level - 1}"/>`.repeat(10)}</g>`;
    }
    assert.throws(() => importSvgAsset(wrap(`<defs>${body}</defs><use href="#l8"/>`)),
        /expand to more than/);
    assert.throws(() => importSvgAsset(wrap(' '.repeat(SVG_ASSET_LIMITS.maxBytes))),
        /exceeds/);
    const deep = '<g>'.repeat(SVG_ASSET_LIMITS.maxDepth + 1) + '</g>'.repeat(SVG_ASSET_LIMITS.maxDepth + 1);
    assert.throws(() => importSvgAsset(wrap(deep)), /nest deeper/);
});

test('path data normalizes to absolute commands with a tight bounding box', () => {
    const { parsePathData, pathBounds, serializePath } = svgAssetTesting;
    const segments = parsePathData('m10 10 h20 v10 s-10 10 -20 0 q-5 -5 0 -10 a5 5 0 1 1 10 0 z');
    assert.equal(serializePath(segments),
        'M10 10L30 10L30 20C30 20 20 30 10 20Q5 15 10 10A5 5 0 1 1 20 10Z');
    const box = pathBounds(parsePathData('M0 0C0 -10 10 -10 10 0'));
    assert.ok(Math.abs(box.y + 7.5) < 1e-9 && box.height === 7.5 && box.width === 10);
    const arc = pathBounds(parsePathData('M0 0A10 10 0 0 1 20 0'));
    assert.ok(Math.abs(arc.y + 10) < 1e-9 && arc.width === 20);
    assert.equal(parsePathData('L0 0'), null);
});

test('the content hash is SHA-256 of the source text', async () => {
    const { sha256 } = svgAssetTesting;
    for (const text of ['', 'abc', 'é'.repeat(100), await fixture('over-budget')]) {
        assert.equal(sha256(text), `sha256:${createHash('sha256').update(text, 'utf8').digest('hex')}`);
    }
});

const firstPath = (imported) => {
    const visit = (node) => node.kind === 'path' ? node
        : node.children.map(visit).find(Boolean);
    return visit(imported.root);
};
const compiledOps = (imported) => imported.root.children[0].properties.vector.asset.ops;

test('user-space gradient percentages and defaults are of the viewport', () => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="10" viewBox="0 0 100 10">
      <linearGradient id="g" gradientUnits="userSpaceOnUse" x1="0%" x2="100%">
        <stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient>
      <linearGradient id="d" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient>
      <rect width="100" height="5" fill="url(#g)"/><rect y="5" width="100" height="5" fill="url(#d)"/></svg>`;
    const ops = compiledOps(importSvgAsset(svg)).filter((op) => op.op === 'path');
    assert.deepEqual(ops[0].fill.transform, [100, 0, 0, 100, 0, -50]);
    assert.deepEqual(ops[1].fill.transform, [100, 0, 0, 100, 0, -50]);
});

test('paths and compiled assets stay within the renderer budgets', () => {
    const long = `M0 0${'L1 1'.repeat(SVG_ASSET_LIMITS.maxPathCommands)}`;
    assert.throws(() => importSvgAsset(wrap(`<path d="${long}"/>`)), /exceeds 4096 commands/);
    const many = '<rect width="1" height="1"/>'.repeat(SVG_ASSET_LIMITS.maxCompiledOps);
    assert.throws(() => importSvgAsset(wrap(many), { nodeBudget: 1 }), /16384 operations/);
    const raster = importSvgAsset(wrap(many), {
        nodeBudget: 1, rasterize: () => ({ contentHash: `sha256:${'e'.repeat(64)}` }) });
    assert.equal(raster.mode, 'rasterized');
});

test('colours, fallbacks and gradient chains resolve as SVG defines them', () => {
    const fill = (value) => firstPath(importSvgAsset(wrap(
        `<rect width="5" height="5" fill="${value}"/>`))).properties.path.fill;
    assert.equal(fill('hsl(.5turn, 100%, 50%)'), '#00ffff');
    assert.equal(fill('hsl(0, 200%, 50%)'), '#ff0000');
    assert.equal(fill('hsl(3.14159265rad, 100%, 50%)'), '#00ffff');
    const fallback = firstPath(importSvgAsset(wrap(
        '<linearGradient id="empty"/><rect width="5" height="5" fill="url(#empty) red"/>')));
    assert.equal(fallback.properties.path.fill, '#ff0000');
    let chain = '<linearGradient id="g0"><stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient>';
    for (let index = 1; index <= 40; index += 1) {
        chain += `<linearGradient id="g${index}" href="#g${index - 1}"/>`;
    }
    const chained = firstPath(importSvgAsset(wrap(`${chain}<rect width="5" height="5" fill="url(#g40)"/>`)));
    assert.equal(chained.properties.path.fill.stops.length, 2);
});

test('invalid declarations keep the inherited value and are losses', () => {
    const imported = svgAssetToDesignIR(wrap(
        '<g fill-opacity=".5"><rect width="5" height="5" fill="#000000" fill-opacity="bogus" stroke-linecap="pointy"/></g>'),
        { source });
    assert.deepEqual(codes(imported.document), ['svg-property-invalid', 'svg-property-invalid']);
    const visit = (node) => node.kind === 'path' ? node : node.children.map(visit).find(Boolean);
    assert.equal(visit(imported.document.root).properties.path.fill, '#00000080');
});

test('oversized transforms are dropped or refused and unit offsets are losses', () => {
    const skew = svgAssetToDesignIR(wrap('<g transform="skewX(90)"><rect width="1" height="1"/></g>'), { source });
    assert.deepEqual(codes(skew.document), ['svg-transform-dropped']);
    const nested = '<g transform="scale(1000)">'.repeat(4) + '<rect width="1" height="1"/>' + '</g>'.repeat(4);
    assert.throws(() => importSvgAsset(wrap(nested)), /compose beyond/);
    const unit = svgAssetToDesignIR(wrap('<rect id="r" width="1" height="1"/><use href="#r" x="1em"/>'), { source });
    assert.deepEqual(codes(unit.document), ['svg-length-unit-dropped']);
});

test('rasterizing keeps the earlier losses and authored ids never collide with generated ones', () => {
    const raster = svgAssetToDesignIR(wrap(
        '<rect width="5" height="5" filter="url(#f)"><animate attributeName="x" to="1"/></rect><filter id="f"/>'),
        { source, rasterize: () => ({ contentHash: `sha256:${'f'.repeat(64)}` }) });
    assert.equal(raster.mode, 'rasterized');
    assert.deepEqual(codes(raster.document), ['svg-animation-dropped', 'svg-rasterized']);
    const ids = svgAssetToDesignIR(wrap(
        '<rect id="a" transform="translate(1 1)" width="1" height="1"/><rect id="a:shape" width="1" height="1"/>' +
        '<rect id="a.0" width="1" height="1"/>'), { source });
    const all = [...indexTree(ids.document.root).index.keys()];
    assert.equal(new Set(all).size, all.length);
    assert.ok(all.includes('assets/svg:a:shape') && all.includes('assets/svg:a::shape'));
});
