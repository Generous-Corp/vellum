import assert from 'node:assert/strict';
import test from 'node:test';

import { importSvgAsset, SVG_ASSET_LIMITS, SvgAssetRefusal } from '../src/index.js';

const wrap = (body, attributes = '') =>
    `<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"${attributes}>${body}</svg>`;
const raster = { contentHash: `sha256:${'c'.repeat(64)}` };
const codes = (diagnostics) => diagnostics.map((item) => item.code).sort();

// What the rasterizer hook is given, or null when the asset is refused first.
function handed(svg) {
    let input = null;
    try {
        importSvgAsset(svg, { sourceKey: 's', rasterize: (text) => { input = text; return raster; } });
    } catch (error) {
        if (!(error instanceof SvgAssetRefusal)) throw error;
        assert.equal(input, null, 'a refused asset never reaches the rasterizer');
        return null;
    }
    return input;
}

const ALLOWED_TAGS = new Set(['svg', 'g', 'defs', 'symbol', 'use', 'switch', 'path', 'rect',
    'circle', 'ellipse', 'line', 'polyline', 'polygon', 'clipPath', 'mask', 'linearGradient',
    'radialGradient', 'stop', 'pattern', 'marker', 'text', 'tspan', 'textPath', 'filter']);

// Checks the rasterizer's input without the importer's own code: known tags,
// no handlers or styles, every reference local, nothing that can name a
// resource outside the asset.
function assertContained(bytes, name) {
    const body = bytes.replace(' xmlns="http://www.w3.org/2000/svg"', '')
        .replace(' xmlns:xlink="http://www.w3.org/1999/xlink"', '');
    assert.doesNotMatch(body, /\\|\/\/|javascript|data:|@|image-set|image\(|expression|<!|<\?/i, name);
    for (const [, tag] of body.matchAll(/<\/?([A-Za-z][\w:-]*)/g)) {
        assert.ok(ALLOWED_TAGS.has(tag) || /^fe[A-Z]\w+$/.test(tag), `${name}: <${tag}>`);
        assert.notEqual(tag, 'feImage', name);
    }
    for (const [, attribute, value] of body.matchAll(/\s([A-Za-z][\w:-]*)="([^"]*)"/g)) {
        assert.ok(!/^on/i.test(attribute) && attribute !== 'style' && !attribute.startsWith('xmlns'),
            `${name}: ${attribute}`);
        if (/href$/.test(attribute)) assert.match(value, /^#[\p{L}\p{N}_.:-]+$/u, `${name}: ${attribute}`);
        for (const [url] of value.matchAll(/url\([^)]*\)?/g)) {
            assert.match(url, /^url\(#[\p{L}\p{N}_.:-]+\)$/u, `${name}: ${url}`);
        }
    }
    assert.equal(handed(bytes), bytes, `${name}: the rasterizer's input imports to itself`);
}

const reported = {
    'script under an editor metadata element': '<svg xmlns="http://www.w3.org/2000/svg" ' +
        'xmlns:i="http://www.inkscape.org/namespaces/inkscape" width="10" height="10">' +
        '<i:x><script>alert(1)</script><i:y href="https://example.invalid/x"/></i:x><text>x</text></svg>',
    'image-set in a style element': wrap(
        '<style>rect { background: image-set("https://example.invalid/x.png" 1x) }</style><text>x</text>'),
    'an image function in a presentation attribute':
        wrap('<rect width="10" height="10" stroke="image(https://example.invalid/x.png)"/><text>x</text>'),
    'an unknown attribute holding a URL':
        wrap('<rect width="10" height="10" foo="https://example.invalid/x"/><text>x</text>'),
    'a link and an image': wrap('<a href="#r"><rect id="r" width="1" height="1"/></a>' +
        '<image href="#r" width="1" height="1"/><text>x</text>'),
    'a filter image and an animation to a local paint': wrap(
        '<linearGradient id="g"><stop/></linearGradient><rect id="q" width="1" height="1"/>' +
        '<filter id="f"><feImage href="#q"/><feGaussianBlur stdDeviation="1"/></filter>' +
        '<rect width="5" height="5" filter="url(#f)"><set attributeName="fill" to="url(#g)"/></rect>'),
    'text that looks like markup': wrap('<text>&lt;script&gt;&amp;<tspan>b</tspan> c</text>'),
};

for (const [name, svg] of Object.entries(reported)) {
    test(`the rasterizer gets only allowlisted content: ${name}`, () => {
        const bytes = handed(svg);
        assert.notEqual(bytes, null, name);
        assertContained(bytes, name);
        assert.notEqual(bytes, svg);
    });
}

test('what the rasterizer was not given is reported', () => {
    const imported = importSvgAsset(reported['an image function in a presentation attribute'],
        { rasterize: () => raster });
    const loss = imported.diagnostics.find((item) => item.code === 'svg-raster-input-sanitized');
    assert.match(loss.message, /stroke on <rect>/);
    assert.equal(loss.disposition, 'unsupported');
    const text = handed(reported['text that looks like markup']);
    assert.match(text, /<text>&lt;script&gt;&amp;<tspan>b<\/tspan> c<\/text>/);
});

const refused = {
    'a CSS-escaped external paint': wrap(
        '<rect width="10" height="10" fill="u\\72l(https://example.invalid/x.svg#g)"/><text>x</text>'),
    'a CSS escape written as a character reference': wrap(
        '<rect width="10" height="10" fill="u&#x5c;72l(https://example.invalid/x.svg#g)"/><text>x</text>'),
    'an escape in a stop colour': wrap('<linearGradient id="g"><stop stop-color="\\72ed"/></linearGradient><text>x</text>'),
    'image-set in a style attribute': '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" ' +
        'style="background-image:image-set(\'https://example.invalid/x.png\' 1x)"><text>x</text></svg>',
    'a CSS comment in a style attribute': wrap('<rect width="1" height="1" style="fill:/**/red"/><text>x</text>'),
    'an at-rule in a style attribute': wrap('<rect width="1" height="1" style="@import x;fill:red"/><text>x</text>'),
    'an external filter after a filter function': wrap(
        '<rect width="1" height="1" filter="blur(1px) url(https://example.invalid/f.svg#f)"/>'),
    'an external cursor': wrap('<rect width="1" height="1" cursor="url(https://example.invalid/c.cur)"/><text>x</text>'),
    'an id that breaks out of url()': wrap('<rect id="a)b" width="1" height="1"/><text>x</text>'),
};

for (const [name, svg] of Object.entries(refused)) {
    test(`refuses ${name} before the rasterizer`, () => {
        assert.equal(handed(svg), null, name);
    });
}

function timed(svg, pattern) {
    const start = performance.now();
    assert.throws(() => importSvgAsset(svg, { sourceKey: 's', rasterize: () => raster }), pattern);
    return performance.now() - start;
}

test('every reference kind shares one expansion budget and is refused quickly', () => {
    const letters = 'abcdefghijklmnopqrstu';
    const doubling = (element, attribute, leaf) => {
        let body = leaf;
        for (let index = 1; index < letters.length; index += 1) {
            const child = `<g ${attribute}="url(#${letters[index - 1]})"/>`;
            body += `<${element} id="${letters[index]}">${child}${child}</${element}>`;
        }
        return `${body}<g ${attribute}="url(#u)"/>`;
    };
    const bombs = {
        mask: doubling('mask', 'mask', '<mask id="a"/>'),
        clip: doubling('clipPath', 'clip-path', '<clipPath id="a"/>'),
        pattern: doubling('pattern', 'fill', '<pattern id="a"/>'),
        marker: doubling('marker', 'marker-start', '<marker id="a"/>'),
        filter: doubling('filter', 'filter', '<filter id="a"/>'),
    };
    for (const [name, body] of Object.entries(bombs)) {
        const elapsed = timed(wrap(body), /expand to more than/);
        assert.ok(elapsed < 1000, `${name} took ${elapsed} ms`);
    }
    let chain = '<mask id="m0"><rect width="1" height="1" fill="white"/></mask>';
    for (let index = 1; index < 100; index += 1) {
        chain += `<mask id="m${index}"><g mask="url(#m${index - 1})"><rect width="1" height="1"/></g></mask>`;
    }
    const elapsed = timed(wrap(`${chain}<rect width="1" height="1" mask="url(#m99)"/>`), /nest deeper than/);
    assert.ok(elapsed < 1000, `the mask chain took ${elapsed} ms`);
    const style = wrap('<mask id="a"/><mask id="b"><g style="mask:url(#a)"/></mask><g style="mask: url(#b)" mask="url(#a)"/>');
    assert.equal(importSvgAsset(style, { sourceKey: 's' }).mode, 'subtree', 'a short chain still imports');
    assert.throws(() => importSvgAsset(wrap('<mask id="m"><g style="mask:url(#m)"/></mask>')), /reference cycle/);
});

test('a clip-path inside a clipPath is rasterized or reported, never silently dropped', () => {
    const inner = '<clipPath id="i"><rect width="5" height="10"/></clipPath>';
    const cases = {
        'on a clip shape': `${inner}<clipPath id="o"><rect width="10" height="10" clip-path="url(#i)"/></clipPath>`,
        'on a clip use': `${inner}<rect id="s" width="10" height="10"/>` +
            '<clipPath id="o"><use href="#s" clip-path="url(#i)"/></clipPath>',
        'on the shape a clip use names': `${inner}<defs><rect id="s" width="10" height="10" clip-path="url(#i)"/></defs>` +
            '<clipPath id="o"><use href="#s"/></clipPath>',
    };
    for (const [name, defs] of Object.entries(cases)) {
        const svg = wrap(`${defs}<rect width="10" height="10" fill="red" clip-path="url(#o)"/>`);
        let calls = 0;
        const rasterized = importSvgAsset(svg, { rasterize: () => { calls += 1; return raster; } });
        assert.equal(rasterized.mode, 'rasterized', name);
        assert.equal(calls, 1, name);
        const dropped = importSvgAsset(svg);
        const loss = dropped.diagnostics.find((item) => item.code === 'svg-raster-feature-dropped');
        assert.match(loss?.message ?? '', /clip-path inside a clipPath/, name);
    }
    const plain = importSvgAsset(wrap('<clipPath id="o"><rect width="5" height="10"/></clipPath>' +
        '<rect width="10" height="10" clip-path="url(#o)"/>'));
    assert.deepEqual(codes(plain.diagnostics), [], 'a clip without nested clipping keeps its subtree');
});

test('mask content inherits presentation from the mask element ancestors', () => {
    const svg = wrap('<defs fill="white"><mask id="m"><rect width="10" height="10"/></mask></defs>' +
        '<rect width="10" height="10" fill="red" mask="url(#m)"/>');
    const mask = JSON.stringify(importSvgAsset(svg, { sourceKey: 's' }).root);
    assert.match(mask, /"mask":"luminance"/);
    const fills = [...mask.matchAll(/"fill":"([^"]+)"/g)].map((match) => match[1]);
    assert.deepEqual(fills.sort(), ['#ff0000', '#ffffff']);
});

test('a dash pattern denser than the native limit is rasterized or dropped, in both forms', () => {
    const dense = wrap('<path d="M0 5H10" fill="none" stroke="red" stroke-dasharray=".00001 .00001"/>');
    const denseArc = wrap('<circle cx="5" cy="5" r="4" fill="none" stroke="red" stroke-dasharray=".0001"/>');
    for (const svg of [dense, denseArc]) {
        for (const options of [{}, { nodeBudget: 1 }]) {
            const imported = importSvgAsset(svg, options);
            assert.doesNotMatch(JSON.stringify(imported.root), /strokeDasharray/, JSON.stringify(options));
            const loss = imported.diagnostics.find((item) => item.code === 'svg-raster-feature-dropped');
            assert.match(loss?.message ?? '', /dashes, beyond 90000/);
            assert.equal(importSvgAsset(svg, { ...options, rasterize: () => raster }).mode, 'rasterized');
        }
    }
    for (const options of [{}, { nodeBudget: 1 }]) {
        const sparse = importSvgAsset(wrap('<path d="M0 5H10" fill="none" stroke="red" stroke-dasharray="1 1"/>'), options);
        assert.match(JSON.stringify(sparse.root), /"strokeDasharray":\[1,1\]/);
        const odd = importSvgAsset(wrap('<path d="M0 5H10" fill="none" stroke="red" stroke-dasharray=".0002"/>'), options);
        assert.match(JSON.stringify(odd.root), /"strokeDasharray":\[0.0002\]/, 'an odd array counts its doubled period');
    }
});

test('tiny geometry keeps its precision before a large transform, in plain notation', () => {
    const tiny = importSvgAsset(wrap('<rect width=".0000001" height=".0000001" fill="red"/>',
        ' viewBox="0 0 .0000001 .0000001"'));
    const text = JSON.stringify(tiny.root);
    assert.match(text, /"d":"M0 0L0.0000001 0L0.0000001 0.0000001L0 0.0000001Z"/);
    assert.match(text, /matrix\(100000000, 0, 0, 100000000, 0, 0\)/);
    const scaled = importSvgAsset(wrap('<g transform="scale(100000)"><rect width=".00001234567" ' +
        'height=".00001" fill="red"/></g>'), { nodeBudget: 1 });
    const op = scaled.root.children[0].properties.vector.asset.ops.find((item) => item.op === 'path');
    assert.equal(op.d, 'M0 0L0.00001234567 0L0.00001234567 0.00001L0 0.00001Z');
    assert.doesNotMatch(op.d, /e/);
    assert.ok(SVG_ASSET_LIMITS.maxDashSegments < 100000);
});
