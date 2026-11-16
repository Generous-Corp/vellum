import assert from 'node:assert/strict';
import test from 'node:test';

import { importSvgAsset, SVG_ASSET_LIMITS, svgAssetToDesignIR, SvgAssetRefusal } from '../src/index.js';
import { svgAssetTesting } from '../src/svg-asset.js';

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

test('a paint fallback is a colour or none, so a chain of references cannot recurse', () => {
    const chained = wrap(`<rect width="1" height="1" fill="${'url(#x) '.repeat(50000)}red"/>`);
    const imported = importSvgAsset(chained);
    assert.ok(codes(imported.diagnostics).includes('svg-property-invalid'));
    const fallback = importSvgAsset(wrap('<rect width="1" height="1" fill="url(#missing) red"/>'));
    assert.match(JSON.stringify(fallback.root), /"fill":"#ff0000"/, 'a colour fallback still paints');
});

test('the rasterizer is never asked for more pixels than the raster budget', () => {
    const sized = (width, height) => `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><text>x</text></svg>`;
    let calls = 0;
    const rasterize = () => { calls += 1; return raster; };
    assert.equal(importSvgAsset(sized(8192, 2048), { rasterize }).mode, 'rasterized', 'at the pixel limit');
    for (const [width, height] of [[1000000, 1000000], [8193, 1], [8192, 2049]]) {
        assert.throws(() => importSvgAsset(sized(width, height), { rasterize }), /rasterizing at/);
    }
    assert.equal(calls, 1);
    assert.equal(importSvgAsset(sized(1000000, 1000000)).mode, 'subtree', 'without a rasterizer the text is a loss');
});

test('inherit takes the parent value for a property that does not inherit', () => {
    const stops = importSvgAsset(wrap('<linearGradient id="g" stop-color="red"><stop offset="0" stop-color="inherit"/>' +
        '<stop offset="1" stop-color="blue"/></linearGradient><rect width="10" height="10" fill="url(#g)"/>'));
    const text = JSON.stringify(stops.root);
    assert.match(text, /#ff0000/);
    assert.doesNotMatch(text, /#000000/);
    const faded = importSvgAsset(wrap('<g opacity="0.5"><g opacity="inherit"><rect width="1" height="1"/></g></g>'));
    assert.equal((JSON.stringify(faded.root).match(/"opacity":0.5/g) ?? []).length, 2);
});

test('a clip-path on a clipPath, as an attribute or a style, is rasterized or reported', () => {
    for (const clip of ['clip-path="url(#i)"', 'style="clip-path:url(#i)"']) {
        const svg = wrap(`<defs><clipPath id="i"><rect width="5" height="10"/></clipPath><clipPath id="o" ${clip}>` +
            '<rect width="10" height="10"/></clipPath></defs><rect width="10" height="10" clip-path="url(#o)"/>');
        assert.equal(importSvgAsset(svg, { rasterize: () => raster }).mode, 'rasterized', clip);
        const loss = importSvgAsset(svg).diagnostics.find((item) => item.code === 'svg-raster-feature-dropped');
        assert.match(loss?.message ?? '', /clip-path on a clipPath/, clip);
    }
});

test('a stylesheet is reported wherever it is, definitions included', () => {
    const imported = importSvgAsset(wrap('<defs><style>rect{fill:red}</style></defs><rect width="10" height="10"/>'));
    assert.deepEqual(codes(imported.diagnostics), ['svg-style-element-dropped']);
});

test('inherit resolves each element once, so nested inherits cost linear time', () => {
    const level = '<g a="inherit" b="inherit" c="inherit" d="inherit" e="inherit" f="inherit" opacity="inherit">';
    for (const depth of [12, 60]) {
        const svg = wrap(`${level.repeat(depth)}<rect width="1" height="1"/>${'</g>'.repeat(depth)}`);
        const start = performance.now();
        importSvgAsset(svg);
        const elapsed = performance.now() - start;
        assert.ok(elapsed < 1000, `${depth} levels took ${elapsed} ms`);
    }
});

test('a later normal style declaration does not override an important one', () => {
    const hidden = importSvgAsset('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" ' +
        'style="display:none!important;display:inline"><rect width="10" height="10" fill="red"/></svg>');
    assert.doesNotMatch(JSON.stringify(hidden.root), /"kind":"path"/);
    const red = importSvgAsset(wrap('<rect width="1" height="1" style="fill:red !important;fill:blue"/>'));
    assert.match(JSON.stringify(red.root), /#ff0000/);
    const blue = importSvgAsset(wrap('<rect width="1" height="1" style="fill:red;fill:blue"/>'));
    assert.match(JSON.stringify(blue.root), /#0000ff/, 'without importance the later declaration wins');
});

test('a use sets the width and height of the svg it names', () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><defs><svg id="s" width="10" ' +
        'height="10" viewBox="0 0 10 10"><rect width="10" height="10"/></svg></defs><use href="#s" width="20" height="20"/></svg>';
    const text = JSON.stringify(importSvgAsset(svg).root);
    assert.match(text, /"clipPath":"M0 0L20 0L20 20L0 20Z"/);
    assert.match(text, /matrix\(2, 0, 0, 2, 0, 0\)/);
    const own = JSON.stringify(importSvgAsset(svg.replace(' width="20" height="20"', '')).root);
    assert.match(own, /"clipPath":"M0 0L10 0L10 10L0 10Z"/, 'without them the svg keeps its own size');
});

test('a clipPath transform is read from its style as well as its attribute', () => {
    for (const transform of ['style="transform:matrix(1,0,0,1,5,0)"', 'transform="translate(5 0)"']) {
        const svg = wrap(`<defs><clipPath id="c" ${transform}><rect width="5" height="10"/></clipPath></defs>` +
            '<rect width="10" height="10" clip-path="url(#c)"/>');
        assert.match(JSON.stringify(importSvgAsset(svg, { sourceKey: 's' }).root), /matrix\(1, 0, 0, 1, 5, 0\)/, transform);
    }
});

const N = 100000;
const PATHOLOGICAL = {
    'spaces': ' '.repeat(N),
    'an unterminated url(': `url(${' '.repeat(N)}!`,
    'an unterminated local url(': `url(#a${' '.repeat(N)}x`,
    'nested parentheses': '('.repeat(N),
    'unbalanced parentheses': `${'(('.repeat(N / 2)})`,
    'closing parentheses': ')'.repeat(N),
    'an unterminated colour function': `rgb(${' '.repeat(N)}!`,
    'a long digit run': `${'1'.repeat(N)}x`,
    'a long fraction before a percent': `1.${'1'.repeat(N)}%x`,
    'a long exponent': `1e${'1'.repeat(N)}`,
    'repeated comment openers': '<!--'.repeat(N / 4),
    'repeated CSS comment openers': '/*'.repeat(N / 2),
    'a long scheme without a colon': `${'a'.repeat(N)}!`,
    'an unterminated tag of attributes': `<a${' b="c"'.repeat(N / 6)}`,
    'an unterminated tag of spaces': `<a${' '.repeat(N)}`,
    'a near-miss important': `x${' '.repeat(N)}!importan`,
    'repeated ampersands': '&amp'.repeat(N / 4),
    'repeated url openers': 'url('.repeat(N / 4),
    'repeated transforms': 'matrix('.repeat(N / 7),
};

// The fastest of three runs, so a busy machine does not fail a linear scan;
// a backtracking one takes seconds to minutes on these inputs.
function fastest(run) {
    let best = Infinity;
    for (let attempt = 0; attempt < 3 && best >= 200; attempt += 1) {
        const start = performance.now();
        try { run(); } catch (error) {
            if (!(error instanceof SvgAssetRefusal) && !(error instanceof TypeError)) throw error;
        }
        best = Math.min(best, performance.now() - start);
    }
    return best;
}

test('every scanner that reads asset text runs in linear time on pathological input', () => {
    for (const [scanner, scan] of Object.entries(svgAssetTesting.scanners)) {
        for (const [shape, input] of Object.entries(PATHOLOGICAL)) {
            const elapsed = fastest(() => scan(input));
            assert.ok(elapsed < 200, `${scanner} on ${shape} took ${elapsed.toFixed(1)} ms`);
        }
    }
});

test('a whole import of pathological attribute values stays fast', () => {
    const svgs = { 'the reported unterminated url(': `<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(${' '.repeat(N)}!"/></svg>` };
    for (const [shape, input] of Object.entries(PATHOLOGICAL)) {
        if (/[<&"]/.test(input)) continue;
        for (const attribute of ['fill', 'style', 'transform', 'd', 'points', 'stop-color', 'clip-path', 'id']) {
            svgs[`${shape} in ${attribute}`] = wrap(`<path ${attribute}="${input}"/>`);
        }
    }
    for (const [shape, input] of Object.entries(PATHOLOGICAL)) {
        const body = input.replace(/[<&]/g, ' ');
        svgs[`${shape} as title text`] = wrap(`<title>${body}</title><rect width="1" height="1"/>`);
        svgs[`${shape} as text`] = wrap(`<text>${body}</text>`);
    }
    const source = { key: 's', adapter: 'svg', adapterVersion: '1', formatVersion: '1.1', revision: 't' };
    for (const [name, svg] of Object.entries(svgs)) {
        const imported = fastest(() => importSvgAsset(svg, { sourceKey: 's', rasterize: () => raster }));
        assert.ok(imported < 200, `${name} took ${imported.toFixed(1)} ms`);
        const normalized = fastest(() => svgAssetToDesignIR(svg, { source, rasterize: () => raster }));
        assert.ok(normalized < 200, `${name} through DesignIR took ${normalized.toFixed(1)} ms`);
    }
});

test('identities from distinct use paths never collide', () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg"><defs><rect id="b" width="1" height="1"/>' +
        '<rect id=":b" width="1" height="1"/></defs><use id="a:" href="#b"/><use id="a" href="#:b"/></svg>';
    const ids = [];
    const visit = (node) => { ids.push(node.sourceId); node.children.forEach(visit); };
    visit(importSvgAsset(svg).root);
    assert.equal(new Set(ids).size, ids.length, ids.join(' '));
    assert.doesNotThrow(() => svgAssetToDesignIR(svg, { source: { key: 's', adapter: 'svg', adapterVersion: '1',
        formatVersion: '1.1', revision: 't' } }));
    assert.ok(ids.includes('svg:2_a::1_b') && ids.includes('svg:1_a:2_:b'), ids.join(' '));
});

test('a symbol without a viewBox sets the viewport its instance clips to and its percentages use', () => {
    const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="20"><linearGradient id="g" ' +
        'gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="red"/><stop offset="1" stop-color="blue"/>' +
        '</linearGradient><symbol id="s"><rect width="40" height="10" fill="url(#g)"/></symbol>' +
        '<use href="#s" width="20" height="10"/></svg>';
    const ops = importSvgAsset(svg).root.children[0].properties.vector.asset.ops;
    assert.ok(ops.some((op) => op.clip?.d === 'M0 0L20 0L20 10L0 10Z'), JSON.stringify(ops));
    const ramp = ops.find((op) => op.op === 'path').fill;
    assert.equal(ramp.transform[0], 20, 'the ramp ends at 100% of the 20-unit symbol viewport');
    const visible = importSvgAsset(svg.replace('<symbol id="s">', '<symbol id="s" overflow="visible">'));
    assert.ok(!visible.root.children[0].properties.vector.asset.ops.some((op) => op.clip), 'overflow visible does not clip');
});

test('geometry set in a style is rasterized or a loss, never silently dropped', () => {
    const svg = wrap('<rect style="width:10px;height:10px;fill:red"/>');
    const dropped = importSvgAsset(svg);
    const loss = dropped.diagnostics.filter((item) => item.code === 'svg-raster-feature-dropped');
    assert.ok(loss.some((item) => /width set in a style/.test(item.message)), JSON.stringify(dropped.diagnostics));
    const bytes = handed(svg);
    assert.match(bytes, /<rect width="10px" height="10px" fill="#ff0000"\/>/);
    const clip = wrap('<clipPath id="c"><rect style="width:5px" height="10"/></clipPath><rect width="10" height="10" clip-path="url(#c)"/>');
    assert.equal(importSvgAsset(clip, { rasterize: () => raster }).mode, 'rasterized');
    const unread = importSvgAsset(wrap('<polyline style="points:0 0 5 5" points="0 0 10 10" stroke="red"/><rect width="1" height="1" bogus="1"/>'));
    assert.deepEqual(codes(unread.diagnostics), ['svg-attribute-dropped', 'svg-attribute-dropped']);
    const read = importSvgAsset(wrap('<rect width="1" height="1" style="fill:red;opacity:.5" font-size="3"/>'));
    assert.deepEqual(codes(read.diagnostics), [], 'what lowering reads, or only text uses, is no loss');
    const sized = importSvgAsset(wrap('<rect width="1" height="1"/>', ' style="width:24px"'), { rasterize: () => raster });
    assert.equal(sized.mode, 'subtree', 'a CSS size on the root is a loss, not a reason to rasterize');
    assert.deepEqual(codes(sized.diagnostics), ['svg-attribute-dropped']);
});

test('a two-circle gradient whose circles become identical once written paints its last stop with a loss', () => {
    const radial = (fr) => wrap(`<radialGradient id="g" r=".5" fr="${fr}"><stop offset="0" stop-color="red"/>` +
        '<stop offset="1" stop-color="blue"/></radialGradient><rect width="10" height="10" fill="url(#g)"/>');
    const degenerate = importSvgAsset(radial('.49999999'));
    const op = degenerate.root.children[0].properties.vector.asset.ops.find((item) => item.op === 'path');
    assert.equal(op.fill, '#0000ff');
    assert.ok(codes(degenerate.diagnostics).includes('svg-gradient-degenerate'));
    const distinct = importSvgAsset(radial('.4'));
    const kept = distinct.root.children[0].properties.vector.asset.ops.find((item) => item.op === 'path');
    assert.equal(kept.fill.gradient.kind, 'two-circle-radial');
    assert.ok(!codes(distinct.diagnostics).includes('svg-gradient-degenerate'));
});

test('transforms inside mask content and clip shapes are held to the composed-matrix limit', () => {
    const nested = (depth) => `${'<g transform="scale(1000000)">'.repeat(depth)}<rect width="1" height="1" fill="white"/>${'</g>'.repeat(depth)}`;
    const masked = (depth) => wrap(`<mask id="m">${nested(depth)}</mask><rect width="10" height="10" mask="url(#m)"/>`);
    for (const options of [{ sourceKey: 's' }, { sourceKey: 's', nodeBudget: 1 }]) {
        assert.throws(() => importSvgAsset(masked(7), options), /compose beyond/);
        assert.doesNotThrow(() => importSvgAsset(masked(1), options), 'one large scale stays within the limit');
    }
    const clipped = wrap('<g transform="scale(1000000)"><clipPath id="c" transform="scale(1000000)">' +
        '<rect width="1" height="1" transform="scale(1000)"/></clipPath><rect width="1" height="1" clip-path="url(#c)"/></g>');
    assert.throws(() => importSvgAsset(clipped), /compose beyond/);
});

test('a use with a zero width or height on an svg or symbol draws nothing', () => {
    const defs = '<defs><svg id="s" width="10" height="10" viewBox="0 0 10 10"><rect width="10" height="10" fill="red"/></svg>' +
        '<symbol id="y" viewBox="0 0 10 10"><rect width="10" height="10" fill="red"/></symbol>' +
        '<symbol id="n"><rect width="10" height="10" fill="red"/></symbol></defs>';
    for (const target of ['s', 'y', 'n']) {
        for (const options of [{}, { nodeBudget: 1 }]) {
            for (const size of ['width="0" height="20"', 'width="20" height="0"', 'width="0" height="0"', 'width="-1" height="20"']) {
                const text = JSON.stringify(importSvgAsset(wrap(`${defs}<use href="#${target}" ${size}/>`, ''), options).root);
                assert.doesNotMatch(text, /#ff0000/, `${target} ${size} ${JSON.stringify(options)}`);
            }
            const drawn = JSON.stringify(importSvgAsset(wrap(`${defs}<use href="#${target}" width="20" height="20"/>`), options).root);
            assert.match(drawn, /#ff0000/, `${target} with a positive size draws`);
        }
    }
    const own = JSON.stringify(importSvgAsset(wrap(`${defs}<use href="#s"/>`)).root);
    assert.match(own, /#ff0000/, 'omitted dimensions keep the svg size');
});
