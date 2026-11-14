import assert from 'node:assert/strict';
import test from 'node:test';
import { lowerSvgAsset, normalizeImport } from '../src/index.js';

const source = {
    key: 'icon', adapter: 'svg', adapterVersion: '1',
    formatVersion: '1', revision: 'a',
};

test('SVG paths and use lower to stable per-node identities', () => {
    const svg = '<svg id="root"><defs><path id="glyph" d="M0 0L10 10"/></defs>' +
        '<g id="group" opacity="0.5"><use href="#glyph"/>' +
        '<use href="#glyph"/></g></svg>';
    const lowered = lowerSvgAsset(svg);
    assert.equal(lowered.mode, 'subtree');
    const document = normalizeImport({ source, root: lowered.root, diagnostics: lowered.diagnostics });
    const paths = document.root.children[0].children;
    assert.equal(paths.length, 2);
    assert.notEqual(paths[0].id, paths[1].id);
    assert.equal(paths[0].properties.svg.d, 'M0 0L10 10');
    assert.equal(document.lossReport.lossCount, 0);
});

test('SVG budgets select one compiled resource', () => {
    const lowered = lowerSvgAsset('<svg><path d="M0 0L10 10"/><path d="M1 1L2 2"/></svg>',
        { nodeBudget: 2 });
    assert.equal(lowered.mode, 'compiled');
    assert.equal(lowered.root.children.length, 0);
    assert.equal(lowered.diagnostics[0].code, 'svg-compiled-budget');
});

test('SVG sanitizer records unsupported content in DesignIR lossReport', () => {
    const lowered = lowerSvgAsset('<svg><filter id="f"/><path d="M0 0" style="fill:red"/></svg>');
    const document = normalizeImport({ source, root: lowered.root, diagnostics: lowered.diagnostics });
    assert.equal(document.lossReport.lossCount, 2);
    assert.deepEqual(document.lossReport.losses.map((loss) => loss.code),
        ['svg-element-dropped', 'svg-attribute-dropped']);
});

test('SVG sanitizer lowers gradients, clips and masks to primitives', () => {
    const svg = '<svg><defs><clipPath id="clip"><rect x="0" y="0" width="8" height="8"/></clipPath>' +
        '<linearGradient id="paint"><stop offset="0" stop-color="#000"/>' +
        '<stop offset="1" stop-color="#fff"/></linearGradient>' +
        '<mask id="mask"><rect width="8" height="8"/></mask></defs>' +
        '<path id="shape" d="M0 0L8 8" clip-path="url(#clip)" fill="url(#paint)" mask="url(#mask)"/>' +
        '</svg>';
    const lowered = lowerSvgAsset(svg);
    assert.equal(lowered.root.children[0].children[0].properties.svg.clipPath,
        'M0 0h8v8h-8Z');
    assert.equal(lowered.root.children[0].children[0].properties.svg.fill.kind, 'linear');
    assert.equal(lowered.root.children[0].children[1].properties.svg.maskMode, 'alpha');
    const document = normalizeImport({ source, root: lowered.root, diagnostics: lowered.diagnostics });
    assert.equal(document.lossReport.lossCount, 0);
});

test('SVG sanitizer rejects entities, scripts and cyclic use', () => {
    assert.throws(() => lowerSvgAsset('<!DOCTYPE svg><svg/>'), /unsafe/);
    assert.throws(() => lowerSvgAsset('<svg><script/></svg>'), /unsafe/);
    assert.throws(() => lowerSvgAsset('<svg><path d="&external;"/></svg>'), /entity/);
    assert.throws(() => lowerSvgAsset('<svg><use id="a" href="#a"/></svg>'), /cyclic/);
});
