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
        assert.equal(input, '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" ' +
            'width="120" height="60" viewBox="0 0 120 60"><defs><filter id="soft"><feGaussianBlur stdDeviation="2"/>' +
            '</filter></defs><rect id="plate" width="120" height="60" fill="#334155"/>' +
            '<circle id="blurred" cx="30" cy="30" r="20" fill="#f59e0b" filter="url(#soft)"/>' +
            '<text id="caption" x="60" y="35" fill="#ffffff">Hi</text></svg>');
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
    'a CSS-escaped style import': wrap('<style>@\\69mport "https://example.invalid/x.css";</style>' +
        '<text>raster</text>'),
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
    assert.deepEqual(codes(raster.document),
        ['svg-animation-dropped', 'svg-raster-input-sanitized', 'svg-rasterized']);
    const ids = svgAssetToDesignIR(wrap(
        '<rect id="a" transform="translate(1 1)" width="1" height="1"/><rect id="a:shape" width="1" height="1"/>' +
        '<rect id="a.0" width="1" height="1"/>'), { source });
    const all = [...indexTree(ids.document.root).index.keys()];
    assert.equal(new Set(all).size, all.length);
    assert.ok(all.includes('assets/svg:a:shape') && all.includes('assets/svg:a::shape'));
});

test('an authored id="viewport" does not collide with the generated viewport', () => {
    const imported = svgAssetToDesignIR(wrap('<rect id="viewport" width="1" height="1"/>'), { source });
    const all = [...indexTree(imported.document.root).index.keys()];
    assert.equal(new Set(all).size, all.length);
    assert.ok(all.includes('assets/svg:viewport'));
});

test('a root with display none draws nothing, as an attribute or a style', () => {
    for (const attributes of [' display="none"', ' style="display: none"']) {
        const imported = importSvgAsset(wrap('<rect width="10" height="10" fill="red"/>', attributes));
        const paths = [];
        const visit = (node) => {
            if (node.kind === 'path') paths.push(node);
            (node.children ?? []).forEach(visit);
        };
        visit(imported.root);
        assert.deepEqual(paths, [], attributes);
    }
});

test('user-space gradient percentages follow a nested svg or symbol viewport', () => {
    const gradient = `<linearGradient id="g" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient>`;
    const nested = `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="10">${gradient}
      <svg width="20" height="10" viewBox="0 0 10 5"><rect width="10" height="5" fill="url(#g)"/></svg></svg>`;
    const symbol = `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="10">${gradient}
      <symbol id="s" viewBox="0 0 10 5"><rect width="10" height="5" fill="url(#g)"/></symbol>
      <use href="#s" width="20" height="10"/></svg>`;
    for (const [name, svg] of Object.entries({ nested, symbol })) {
        const ops = compiledOps(importSvgAsset(svg)).filter((op) => op.op === 'path');
        // The gradient ends at x=10, the inner viewport's width, not 100.
        assert.equal(ops[0].fill.transform[0], 10, name);
    }
});

test('an animation that could point a reference outside the asset is refused before the rasterizer', () => {
    const animations = {
        'set to': '<set attributeName="href" to="https://example.invalid/x.png"/>',
        'animate values': '<animate attributeName="fill" values="red;https://example.invalid/x.png"/>',
        'animate from': '<animate attributeName="fill" from="//example.invalid/x.png" to="red"/>',
        'xlink href target': '<set attributeName="xlink:href" to="#local"/>',
        'url to': '<animate attributeName="fill" to="url(https://example.invalid/#g)"/>',
    };
    for (const [name, animation] of Object.entries(animations)) {
        let handed = null;
        const svg = wrap(`<image id="i" width="5" height="5" href="#local">${animation}</image>` +
            '<rect id="local" width="1" height="1"/><text>raster</text>');
        assert.throws(() => importSvgAsset(svg, { rasterize: (input) => { handed = input; } }),
            SvgAssetRefusal, name);
        assert.equal(handed, null, `${name} never reaches the rasterizer`);
    }
    const local = wrap('<rect width="5" height="5"><animate attributeName="fill" values="red;blue"/>' +
        '<set attributeName="fill" to="url(#g)"/></rect><linearGradient id="g"/>');
    assert.doesNotThrow(() => importSvgAsset(local), 'colour and local animations still import');
});

test('a symbol viewport clips in output units, before its viewBox transform', () => {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
      <symbol id="s" viewBox="0 0 10 10"><rect x="-5" y="-5" width="30" height="30" fill="#ff0000"/></symbol>
      <use id="u" href="#s" width="20" height="20"/></svg>`;
    const subtree = importSvgAsset(svg, { sourceKey: 'assets' });
    const clips = [];
    const visit = (node) => {
        if (node.properties?.paint?.clipPath) clips.push({ id: node.sourceId, clip: node.properties.paint.clipPath, transform: node.properties.paint.transform });
        (node.children ?? []).forEach(visit);
    };
    visit(subtree.root);
    assert.equal(subtree.mode, 'subtree');
    assert.deepEqual(clips, [{ id: 'svg:u:s.viewport', clip: 'M0 0L20 0L20 20L0 20Z', transform: undefined }]);
    const compiled = compiledOps(importSvgAsset(svg, { nodeBudget: 1 }));
    const clipped = compiled.findIndex((op) => op.clip);
    assert.equal(compiled[clipped].clip.d, 'M0 0L20 0L20 20L0 20Z');
    assert.equal(compiled[clipped].transform, undefined, 'the clip group has no viewBox transform');
    assert.ok(compiled.slice(clipped + 1).some((op) => op.op === 'group' &&
        JSON.stringify(op.transform) === JSON.stringify([2, 0, 0, 2, 0, 0])), 'the viewBox transform comes inside it');
});

test('a user-space gradient stroke on a horizontal or vertical line still paints', () => {
    for (const [name, line] of [['horizontal', 'x1="0" y1="5" x2="10" y2="5"'], ['vertical', 'x1="5" y1="0" x2="5" y2="10"']]) {
        const svg = wrap(`<linearGradient id="g" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="10" y2="10">
            <stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/></linearGradient>
            <line ${line} stroke="url(#g)" stroke-width="2"/>`);
        const imported = importSvgAsset(svg);
        assert.equal(imported.mode, 'compiled', name);
        const [op] = compiledOps(imported).filter((item) => item.op === 'path');
        assert.equal(typeof op.stroke, 'object', `${name} keeps its gradient stroke`);
        assert.equal(op.stroke.gradient.kind, 'linear', name);
    }
    const box = importSvgAsset(wrap(`<linearGradient id="g"><stop offset="0" stop-color="red"/>
        <stop offset="1" stop-color="blue"/></linearGradient><line x1="0" y1="5" x2="10" y2="5" stroke="url(#g)"/>`));
    const path = (node) => node.kind === 'path' ? node : node.children.map(path).find(Boolean);
    assert.equal(path(box.root).properties.path.stroke, 'none',
        'an objectBoundingBox gradient on a line still paints nothing');
});

test('an invisible root is never rasterized, even with a filter', () => {
    for (const attributes of [' display="none" filter="url(#f)"', ' style="display: none; filter: url(#f)"']) {
        let calls = 0;
        const imported = importSvgAsset(wrap('<filter id="f"/><rect width="5" height="5" fill="red"/>', attributes),
            { rasterize: () => { calls += 1; return { contentHash: `sha256:${'a'.repeat(64)}` }; } });
        assert.equal(calls, 0, attributes);
        assert.notEqual(imported.mode, 'rasterized', attributes);
        assert.equal(imported.diagnostics.some((entry) => entry.code === 'svg-raster-feature-dropped'), false);
    }
    let calls = 0;
    importSvgAsset(wrap('<filter id="f"/><rect width="5" height="5"/>', ' filter="url(#f)"'),
        { rasterize: () => { calls += 1; return { contentHash: `sha256:${'a'.repeat(64)}` }; } });
    assert.equal(calls, 1, 'a visible root with a filter is still rasterized');
});

test('an href reaches the XLink namespace only under the xlink prefix', () => {
    const cases = {
        'an aliased XLink prefix': '<svg xmlns="http://www.w3.org/2000/svg" xmlns:r="http://www.w3.org/1999/xlink" width="10" height="10">' +
            '<image r:href="https://example.invalid/x.png" width="10" height="10"/><text>raster</text></svg>',
        'an href under an unknown prefix': '<svg xmlns="http://www.w3.org/2000/svg" xmlns:q="urn:other" width="10" height="10">' +
            '<image q:href="https://example.invalid/x.png" width="10" height="10"/><text>raster</text></svg>',
        'xlink rebound': '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="urn:other" width="10" height="10">' +
            '<image xlink:href="https://example.invalid/x.png" width="10" height="10"/><text>raster</text></svg>',
        'an aliased animated href': '<svg xmlns="http://www.w3.org/2000/svg" xmlns:r="http://www.w3.org/1999/xlink" width="10" height="10">' +
            '<image width="10" height="10"><set attributeName="r:href" to="#x"/></image><text>raster</text></svg>',
    };
    for (const [name, svg] of Object.entries(cases)) {
        let handed = null;
        assert.throws(() => importSvgAsset(svg, { rasterize: (input) => { handed = input; } }), SvgAssetRefusal, name);
        assert.equal(handed, null, `${name} never reaches the rasterizer`);
    }
    assert.doesNotThrow(() => importSvgAsset('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" ' +
        'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" width="10" height="10">' +
        '<rect id="r" inkscape:label="box" width="1" height="1"/><use xlink:href="#r"/></svg>'));
});

test('comments are removed in linear time and malformed ones refused', () => {
    const started = Date.now();
    assert.throws(() => importSvgAsset(wrap('<!--'.repeat(200000))), /unterminated comment/);
    assert.throws(() => importSvgAsset(wrap(`<!--${'<!-- '.repeat(100000)}-->`)), SvgAssetRefusal);
    assert.doesNotThrow(() => importSvgAsset(wrap(`${'<!-- a -->'.repeat(50000)}<rect width="1" height="1"/>`)));
    assert.ok(Date.now() - started < 2000, `took ${Date.now() - started} ms`);
    assert.throws(() => importSvgAsset(wrap('<!-- a -- b -->')), /malformed comment/);
});

function firstFill(imported) {
    const visit = (node) => node.kind === 'path' ? node : (node.children ?? []).map(visit).find(Boolean);
    return visit(imported.root)?.properties.path.fill;
}

test('gradient stops and interpolation resolve along the gradient definition', () => {
    const svg = (extra = '') => wrap(`<defs color="red" ${extra}><linearGradient id="g">
        <stop offset="0" stop-color="currentColor"/><stop offset="1" stop-color="#0000ff"/></linearGradient></defs>
        <rect width="5" height="5" color="blue" fill="url(#g)"/>`);
    assert.equal(firstFill(importSvgAsset(svg())).stops[0].color, '#ff0000', 'subtree');
    const compiled = compiledOps(importSvgAsset(svg(), { nodeBudget: 1 })).find((op) => op.op === 'path');
    assert.equal(compiled.fill.gradient.stops[0].color, '#ff0000', 'compiled');
    const linear = importSvgAsset(svg('color-interpolation="linearRGB"'));
    assert.equal(linear.mode, 'compiled', 'linearRGB on the definition routes to the compiled form');
    assert.equal(compiledOps(linear).find((op) => op.op === 'path').fill.gradient.interpolation, 'linear-srgb');
});

test('a clipPath use composes its transform with x and y, and clip-rule comes from the clipPath', () => {
    const svg = wrap(`<defs><rect id="r" width="2" height="2"/>
        <clipPath id="c"><use href="#r" transform="translate(10 0)" x="1"/></clipPath>
        <clipPath id="e" clip-rule="evenodd"><path d="M0 0H10V10H0Z M2 2H8V8H2Z"/></clipPath></defs>
        <rect width="20" height="20" fill="#ff0000" clip-path="url(#c)"/>
        <rect id="h" width="20" height="20" fill="#00ff00" clip-path="url(#e)"/>`);
    const compiled = compiledOps(importSvgAsset(svg, { nodeBudget: 1 }));
    const clipped = compiled.filter((op) => op.clip);
    const wrapper = compiled.findIndex((op) => op.clip && op.transform);
    assert.deepEqual(compiled[wrapper].transform, [1, 0, 0, 1, 11, 0], 'translate(10 0) then x=1');
    assert.ok(clipped.some((op) => op.clip.rule === 'evenodd'), 'the clipPath clip-rule makes a hole');
    const subtree = importSvgAsset(svg, { sourceKey: 'assets' });
    const holes = [];
    const visit = (node) => {
        if (node.properties?.path?.fillRule === 'evenodd') holes.push(node);
        (node.children ?? []).forEach(visit);
    };
    visit(subtree.root);
    assert.equal(holes.length, 1, 'the subtree lowers the evenodd clip as an evenodd coverage path');
});

test('a nested svg with overflow visible is not clipped, as an attribute or a style', () => {
    const clips = (svg) => {
        const found = [];
        const visit = (node) => {
            if (node.properties?.paint?.clipPath) found.push(node.properties.paint.clipPath);
            (node.children ?? []).forEach(visit);
        };
        visit(importSvgAsset(svg).root);
        return found;
    };
    const nested = (attribute) => `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
        <svg width="20" height="20" ${attribute}><rect width="40" height="40" fill="#ff0000"/></svg></svg>`;
    assert.deepEqual(clips(nested('')), ['M0 0L20 0L20 20L0 20Z']);
    assert.deepEqual(clips(nested('overflow="visible"')), []);
    assert.deepEqual(clips(nested('style="overflow: visible"')), []);
});

test('a symbol keeps its own opacity as one group on each instance', () => {
    const svg = wrap(`<symbol id="s" opacity="0.5"><rect width="4" height="4" fill="#ff0000"/>
        <rect x="2" width="4" height="4" fill="#0000ff"/></symbol><use href="#s"/>`);
    const subtree = importSvgAsset(svg, { sourceKey: 'assets' });
    const groups = [];
    const visit = (node) => {
        if (node.compositing?.opacity !== undefined) groups.push({ id: node.sourceId, opacity: node.compositing.opacity, children: node.children.length });
        (node.children ?? []).forEach(visit);
    };
    visit(subtree.root);
    assert.deepEqual(groups, [{ id: 'svg.1:s.symbol', opacity: 0.5, children: 2 }]);
    const compiled = compiledOps(importSvgAsset(svg, { nodeBudget: 1 }));
    assert.ok(compiled.some((op) => op.op === 'group' && op.opacity === 0.5), 'the compiled form groups it too');
});

const hookNever = (svg, name) => {
    let handed = null;
    assert.throws(() => importSvgAsset(svg, { rasterize: (input) => { handed = input; } }), SvgAssetRefusal, name);
    assert.equal(handed, null, `${name} never reaches the rasterizer`);
};

test('every element and attribute name is resolved to its namespace before any check', () => {
    const svg = (body, declarations = '') =>
        `<svg xmlns="http://www.w3.org/2000/svg"${declarations} width="10" height="10">${body}<text>raster</text></svg>`;
    hookNever(svg('<s:foreignObject width="5" height="5"><h:img src="https://example.invalid/x.png"/></s:foreignObject>',
        ' xmlns:s="http://www.w3.org/2000/svg" xmlns:h="http://www.w3.org/1999/xhtml"'), 'a prefixed SVG foreignObject');
    hookNever(svg('<h:img src="https://example.invalid/x.png"/>', ' xmlns:h="http://www.w3.org/1999/xhtml"'), 'an XHTML element');
    hookNever(svg('<g xmlns="http://www.w3.org/1999/xhtml"><img src="https://example.invalid/x.png"/></g>'), 'a default-namespace switch');
    hookNever(svg('<s:script>1</s:script>', ' xmlns:s="http://www.w3.org/2000/svg"'), 'a prefixed script');
    hookNever(svg('<q:rect width="1" height="1"/>'), 'an unbound element prefix');
    hookNever(svg('<rect q:x="1" width="1" height="1"/>'), 'an unbound attribute prefix');
    hookNever(svg('<rect o:fill="red" width="1" height="1"/>', ' xmlns:o="urn:other"'), 'an attribute in an unknown namespace');
    hookNever(svg('<image r:href="https://example.invalid/x.png" width="1" height="1"/>',
        ' xmlns:r="http://www.w3.org/1999/xlink"'), 'an aliased XLink href');
    const local = importSvgAsset(svg('<rect id="r" width="5" height="5" fill="#ff0000"/><use r:href="#r" x="5"/>',
        ' xmlns:r="http://www.w3.org/1999/xlink"'), { rasterize: () => ({ contentHash: `sha256:${'a'.repeat(64)}` }) });
    assert.equal(local.mode, 'rasterized', 'an aliased local href resolves as xlink:href');
    const metadata = importSvgAsset('<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" ' +
        'xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd" xmlns:dc="http://purl.org/dc/elements/1.1/" ' +
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" width="10" height="10">' +
        '<sodipodi:namedview inkscape:zoom="2"/><metadata><rdf:RDF><dc:title>Icon</dc:title></rdf:RDF></metadata>' +
        '<rect inkscape:label="box" width="5" height="5" fill="#ff0000"/></svg>');
    assert.equal(metadata.mode, 'subtree', 'editor metadata is dropped, not refused');
    assert.deepEqual(metadata.diagnostics, []);
});

test('reference traversal is iterative and bounded: a 10,000-link use chain is refused in linear time', () => {
    const chain = (length) => {
        let body = '<defs>';
        for (let index = 0; index < length; index += 1) body += `<use id="g${index}" href="#g${index + 1}"/>`;
        body += `<rect id="g${length}" width="1" height="1"/></defs><use href="#g0"/>`;
        return wrap(body);
    };
    const started = Date.now();
    assert.throws(() => importSvgAsset(chain(10000)), /nest deeper than 64|expand to more than/);
    assert.ok(Date.now() - started < 3000, `took ${Date.now() - started} ms`);
    assert.doesNotThrow(() => importSvgAsset(chain(30)), 'a chain within the depth limit imports');
    const cycle = (length) => {
        let body = '';
        for (let index = 0; index < length; index += 1) body += `<use id="c${index}" href="#c${(index + 1) % length}"/>`;
        return wrap(body);
    };
    assert.throws(() => importSvgAsset(cycle(10000)), /reference cycle/);
});

test('the expanded nesting limit holds at the limit and refuses one past it', () => {
    const nested = (levels) => {
        let body = '<defs><rect id="u0" width="1" height="1"/>';
        for (let index = 1; index <= levels; index += 1) body += `<use id="u${index}" href="#u${index - 1}"/>`;
        return wrap(`${body}</defs><use href="#u${levels}"/>`);
    };
    // svg, the outer use, then two levels per use link: the deepest legal
    // chain is found by the limit itself.
    let legal = 0;
    for (let levels = 1; levels < 80; levels += 1) {
        try { importSvgAsset(nested(levels)); legal = levels; } catch { break; }
    }
    assert.ok(legal > 0);
    assert.doesNotThrow(() => importSvgAsset(nested(legal)), 'at the limit');
    assert.throws(() => importSvgAsset(nested(legal + 1)), /nest deeper than 64/, 'one past the limit');
});

test('every importer limit matches its consumer and holds at the limit and one past it', () => {
    assert.equal(SVG_ASSET_LIMITS.maxDashes, 64);
    assert.equal(SVG_ASSET_LIMITS.maxGradientStops, 64);
    assert.equal(SVG_ASSET_LIMITS.maxPathCommands, 4096);
    assert.equal(SVG_ASSET_LIMITS.maxCompiledOps, 16384);
    assert.equal(SVG_ASSET_LIMITS.maxCompiledDepth, 64);
    const dashed = (count, options = {}) => importSvgAsset(wrap(
        `<path d="M0 0H10" stroke="#000000" stroke-dasharray="${Array(count).fill(1).join(' ')}"/>`), options);
    for (const options of [{}, { nodeBudget: 1 }]) {
        const atLimit = dashed(64, options);
        const ops = atLimit.mode === 'compiled'
            ? compiledOps(atLimit).filter((op) => op.op === 'path')[0].strokeDasharray
            : firstPathOf(atLimit).properties.path.strokeDasharray;
        assert.equal(ops.length, 64, `64 dashes are kept (${atLimit.mode})`);
        const past = dashed(65, options);
        assert.ok(past.diagnostics.some((entry) => entry.code === 'svg-raster-feature-dropped' &&
            /65 entries/.test(entry.message)), `65 dashes are a recorded loss (${past.mode})`);
        const shown = past.mode === 'compiled'
            ? compiledOps(past).filter((op) => op.op === 'path')[0].strokeDasharray
            : firstPathOf(past).properties.path.strokeDasharray;
        assert.equal(shown, undefined, 'no asset carries more dashes than the consumers accept');
        let rasterized = 0;
        assert.equal(dashed(65, { ...options, rasterize: () => { rasterized += 1; return { contentHash: `sha256:${'b'.repeat(64)}` }; } }).mode,
            'rasterized', 'with a rasterizer, 65 dashes rasterize');
        assert.equal(rasterized, 1);
    }
    const stops = (count) => `<linearGradient id="g">${Array.from({ length: count },
        (_, index) => `<stop offset="${index / (count - 1)}" stop-color="#${index % 2 ? 'ff0000' : '0000ff'}"/>`).join('')}</linearGradient>` +
        '<rect width="5" height="5" fill="url(#g)"/>';
    assert.equal(firstPathOf(importSvgAsset(wrap(stops(64)))).properties.path.fill.stops.length, 64);
    const many = svgAssetToDesignIR(wrap(stops(65)), { source });
    assert.ok(codes(many.document).includes('svg-gradient-stops-dropped'));
    const long = (count) => `<path d="M0 0${'L1 1'.repeat(count - 1)}"/>`;
    assert.doesNotThrow(() => importSvgAsset(wrap(long(SVG_ASSET_LIMITS.maxPathCommands))));
    assert.throws(() => importSvgAsset(wrap(long(SVG_ASSET_LIMITS.maxPathCommands + 1))), /exceeds 4096 commands/);
    assert.throws(() => importSvgAsset(wrap(''), { nodeBudget: SVG_ASSET_LIMITS.maxNodeBudget + 1 }), /budgets/);
    assert.doesNotThrow(() => importSvgAsset(wrap(''), { nodeBudget: SVG_ASSET_LIMITS.maxNodeBudget }));
});

function firstPathOf(imported) {
    const visit = (node) => node.kind === 'path' ? node : (node.children ?? []).map(visit).find(Boolean);
    return visit(imported.root);
}

test('stop positions stay strictly increasing once the consumers read them as float32', () => {
    const svg = wrap(`<linearGradient id="g"><stop offset="0.5" stop-color="#ff0000"/>
        <stop offset="0.50000001" stop-color="#0000ff"/><stop offset="1" stop-color="#00ff00"/></linearGradient>
        <rect width="5" height="5" fill="url(#g)"/>`);
    const imported = importSvgAsset(svg);
    assert.equal(imported.mode, 'compiled', 'the float32 collision routes to the compiled form');
    const distinct = importSvgAsset(svg.replace('0.50000001', '0.5001'));
    assert.equal(distinct.mode, 'subtree');
});

test('identities the consumers would truncate route the asset to the compiled form', () => {
    const longId = 'a'.repeat(250);
    let body = `<defs><rect id="${longId}" width="1" height="1"/>`;
    for (let index = 1; index <= 4; index += 1) body += `<g id="${longId}${index}"><use href="#${index === 1 ? longId : longId + (index - 1)}"/></g>`;
    body += '</defs>';
    for (let index = 0; index < 2; index += 1) body += `<use id="${longId}x${index}" href="#${longId}4"/>`;
    const imported = importSvgAsset(wrap(body));
    assert.equal(imported.mode, 'compiled');
    assert.match(imported.diagnostics.find((entry) => entry.code === 'svg-compiled').message, /identities longer than 900 bytes/);
});

test('a symbol clip and mask are in the symbol user space, inside its viewBox transform', () => {
    const svg = wrap(`<defs><clipPath id="c"><rect width="5" height="10"/></clipPath>
        <mask id="m"><rect width="5" height="10" fill="#ffffff"/></mask></defs>
        <symbol id="s" viewBox="0 0 10 10" clip-path="url(#c)"><rect width="10" height="10" fill="#ff0000"/></symbol>
        <symbol id="t" viewBox="0 0 10 10" mask="url(#m)"><rect width="10" height="10" fill="#0000ff"/></symbol>
        <use href="#s" width="20" height="20"/><use href="#t" y="20" width="20" height="20"/>`)
        .replace('width="10" height="10"', 'width="40" height="40"');
    const compiled = compiledOps(importSvgAsset(svg, { nodeBudget: 1 }));
    const scaled = compiled.findIndex((op) => op.op === 'group' && JSON.stringify(op.transform) === '[2,0,0,2,0,0]');
    assert.ok(scaled >= 0);
    const clip = compiled.slice(scaled + 1).find((op) => op.clip && op.clip.d === 'M0 0L5 0L5 10L0 10Z');
    assert.ok(clip, 'the symbol clip sits inside the viewBox scale, so it covers 10 output units');
    const subtree = importSvgAsset(svg, { sourceKey: 'assets' });
    const chain = [];
    const find = (node, trail) => {
        if (node.properties?.paint?.clipPath === 'M0 0L5 0L5 10L0 10Z') chain.push(...trail);
        for (const child of node.children ?? []) find(child, [...trail, node.properties?.paint?.transform]);
    };
    find(subtree.root, []);
    assert.ok(chain.includes('matrix(2, 0, 0, 2, 0, 0)'), 'the subtree clip is under the viewBox scale');
    const masked = [];
    const findMask = (node, trail) => {
        if (node.compositing?.mask) masked.push(...trail);
        for (const child of node.children ?? []) findMask(child, [...trail, node.properties?.paint?.transform]);
    };
    findMask(subtree.root, []);
    assert.ok(masked.includes('matrix(2, 0, 0, 2, 0, 0)'), 'the subtree mask is under the viewBox scale');
});

test('hidden clip children add no clip area, directly, inherited or through a use', () => {
    const clipShapes = (clip) => {
        const svg = wrap(`<defs><rect id="r" width="4" height="4"/>${clip}</defs>
            <rect width="10" height="10" fill="#ff0000" clip-path="url(#c)"/>`);
        const ops = compiledOps(importSvgAsset(svg, { nodeBudget: 1 }));
        return ops.filter((op) => op.clip).length + ops.filter((op) => op.op === 'group' && op.opacity === 0).length * 100;
    };
    assert.equal(clipShapes('<clipPath id="c"><rect width="4" height="4"/></clipPath>'), 1, 'a visible child clips');
    assert.equal(clipShapes('<clipPath id="c"><rect width="4" height="4" visibility="hidden"/></clipPath>'), 100, 'a hidden child leaves an empty clip');
    assert.equal(clipShapes('<clipPath id="c" visibility="hidden"><rect width="4" height="4"/></clipPath>'), 100, 'inherited visibility');
    assert.equal(clipShapes('<clipPath id="c"><use href="#r" display="none"/></clipPath>'), 100, 'a use with display none');
    assert.equal(clipShapes('<clipPath id="c"><use href="#r" visibility="hidden"/></clipPath>'), 100, 'a hidden use');
    assert.equal(clipShapes('<clipPath id="c" visibility="hidden"><rect width="4" height="4" visibility="visible"/></clipPath>'), 1, 'a child can override');
});
