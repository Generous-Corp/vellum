import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
    createApp, Design, jsx, materializeDesign, mount, Path, Text, TextRun, View,
} from '../src/index.js';
import {
    BLEND_MODES,
    decodeFigmaPluginExport,
    normalizeImport,
    resolveTokenLayers,
} from '../../vellum-design-ir/src/index.js';

const protocol = 'vellum.authoring-host.v1';
const fixtures = join(
    dirname(fileURLToPath(import.meta.url)),
    '..', '..', '..', 'fixtures', 'design-ir',
);

function fixture() {
    return {
        tokens: {
            'main.color.canvas': { $type: 'color', $value: '#0f172a' },
            'main.color.accent': { $type: 'color', $value: '#14b8a6' },
        },
        root: {
            id: 'main/root',
            kind: 'view',
            name: 'Imported screen',
            properties: {
                layout: { display: 'flex', direction: 'column', gap: 12 },
                paint: { backgroundColor: '{main.color.canvas}' },
            },
            children: [{
                id: 'main/create',
                kind: 'button',
                name: 'Create',
                text: 'Create board',
                properties: { paint: { backgroundColor: '{main.color.accent}' } },
                children: [],
            }],
        },
    };
}

test('materializes imported design with stable ids, tokens, viewport, and behavior', () => {
    const app = createApp({
        id: 'design-app',
        stateVersion: '1',
        initialState: { presses: 0 },
        actions: {
            create: (model) => ({ presses: model.presses + 1 }),
        },
        render: () => materializeDesign(fixture(), {
            viewport: { width: 640, height: 400, padding: 20 },
            actions: { 'main/create': { press: 'create' } },
        }),
    });
    const bridge = mount(app);
    let tree = JSON.parse(bridge.renderJSON()).tree;
    assert.equal(tree.id, 'main/root');
    assert.deepEqual(tree.style, {
        backgroundColor: '#0f172a', direction: 'vertical', gap: 12,
        height: 400, padding: 20, width: 640,
    });
    assert.equal(tree.children[0].id, 'main/create');
    assert.equal(tree.children[0].style.backgroundColor, '#14b8a6');
    assert.equal(tree.children[0].style.width, undefined);
    assert.equal(tree.children[0].style.height, undefined);
    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({
        protocol, action: tree.children[0].events.press,
    }))).tree;
    assert.equal(tree.children[0].id, 'main/create');
    assert.equal(JSON.parse(bridge.snapshotStateJSON()).state.model.presses, 1);
});

test('Design preserves imported root, text, and nested flex dimensions', () => {
    const document = fixture();
    document.source = { key: 'main', namespace: 'main', revision: 'revision-a' };
    document.root.properties.layout = {
        display: 'flex',
        direction: 'column',
        gap: 16,
        height: 400,
        padding: 24,
        width: 640,
    };
    document.root.children = [{
        id: 'main/title',
        kind: 'text',
        name: 'Title',
        text: 'Palette Board',
        properties: { text: { color: '#f8fafc', fontSize: 28 } },
        children: [],
    }, {
        id: 'main/cards',
        kind: 'view',
        name: 'Cards',
        properties: {
            layout: {
                direction: 'row',
                display: 'flex',
                gap: 12,
                height: 190,
                width: 592,
            },
        },
        children: [{
            id: 'main/cyan',
            kind: 'view',
            name: 'Cyan',
            properties: {
                layout: { height: 190, width: 286 },
                paint: { backgroundColor: '#38bdf8' },
            },
            children: [],
        }, {
            id: 'main/violet',
            kind: 'view',
            name: 'Violet',
            properties: {
                layout: { height: 190, width: 286 },
                paint: { backgroundColor: '#8b5cf6' },
            },
            children: [],
        }],
    }];
    const app = createApp({
        id: 'design-component-layout',
        stateVersion: '1',
        initialState: {},
        render: () => Design({ document }),
    });
    const tree = JSON.parse(mount(app).renderJSON()).tree;

    assert.deepEqual(tree.style, {
        backgroundColor: '#0f172a',
        direction: 'vertical',
        gap: 16,
        height: 400,
        padding: 24,
        width: 640,
    });
    assert.deepEqual(tree.children[0].style, {
        color: '#f8fafc',
        fontSize: 28,
    });
    assert.deepEqual(tree.children[1].style, {
        direction: 'horizontal',
        gap: 12,
        height: 190,
        width: 592,
    });
    assert.equal(tree.children[1].children[0].style.width, 286);
    assert.equal(tree.children[1].children[1].style.width, 286);

    const materializedTree = JSON.parse(mount(createApp({
        id: 'materialized-design-layout',
        stateVersion: '1',
        initialState: {},
        render: () => materializeDesign(document),
    })).renderJSON()).tree;
    assert.deepEqual(materializedTree, tree);
});

test('renders lowered boxes and leaves display: none nodes out', () => {
    const document = fixture();
    document.root.children = [
        {
            id: 'main/placed', kind: 'view', name: 'Placed',
            properties: { layout: { x: 288, y: 48.5, width: 704, height: 381.875 } },
            children: [],
        },
        {
            id: 'main/hidden', kind: 'text', text: ' ',
            properties: { layout: { display: 'none' } },
            children: [],
        },
    ];
    const tree = JSON.parse(mount(createApp({
        id: 'design-boxes', stateVersion: '1', initialState: {},
        render: () => materializeDesign(document),
    })).renderJSON()).tree;
    assert.deepEqual(tree.children.map((child) => child.id), ['main/placed']);
    assert.deepEqual(tree.children[0].style, { x: 288, y: 48.5, width: 704, height: 381.875 });
});

test('fails closed on unresolved tokens, duplicate ids, and unsupported kinds', () => {
    const unresolved = fixture();
    unresolved.root.properties.paint.backgroundColor = '{missing.token}';
    assert.throws(() => materializeDesign(unresolved), /unresolved design token/);
    assert.throws(() => Design({ document: unresolved }), /unresolved design token/);

    const duplicate = fixture();
    duplicate.root.children.push({ ...duplicate.root.children[0] });
    assert.throws(() => materializeDesign(duplicate), /duplicate materialized design id/);

    const unsupported = fixture();
    unsupported.root.children[0].kind = 'video';
    assert.throws(() => materializeDesign(unsupported), /unsupported materialized design node kind/);

    assert.throws(() => materializeDesign(fixture(), {
        actions: { missing: { press() {} } },
    }), /action target is missing/);
    assert.throws(() => Design({
        actions: { missing() {} },
        document: fixture(),
    }), /binding target is missing/);
});

test('materializes pinned Pulp emitter output into the capture-ready retained host tree', async () => {
    const sourceBytes = await readFile(join(fixtures, 'pulp-emitter-generic.export.json'));
    const sourceHash = createHash('sha256').update(sourceBytes).digest('hex');
    const document = normalizeImport(decodeFigmaPluginExport(
        JSON.parse(sourceBytes.toString('utf8')),
        { sourceHash: `sha256:${sourceHash}`, sourceKey: 'main' },
    ));
    const app = createApp({
        id: 'pulp-emitter-proof',
        stateVersion: '1',
        initialState: { presses: 0 },
        actions: { tap: (model) => ({ presses: model.presses + 1 }) },
        render: () => materializeDesign(document, {
            actions: { 'main/1:2': { press: 'tap' } },
            viewport: { height: 240, padding: 20, width: 420 },
        }),
    });
    const bridge = mount(app);
    let tree = JSON.parse(bridge.renderJSON()).tree;
    assert.equal(tree.type, 'stack');
    assert.equal(tree.style.backgroundColor, '#0f172a');
    assert.equal(tree.children[0].type, 'text');
    assert.equal(tree.children[0].text, 'Emitter Proof');
    assert.equal(tree.children[1].type, 'view');
    assert.equal(typeof tree.events.press, 'string');

    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({
        action: tree.events.press,
        protocol,
    }))).tree;
    assert.equal(tree.id, 'main/1:2');
    assert.equal(JSON.parse(bridge.snapshotStateJSON()).state.model.presses, 1);
});

function tokenSource() {
    return {
        source: {
            key: 'main', adapter: 'fixture', adapterVersion: '1', formatVersion: '1',
            revision: 'tokens-a',
        },
        tokens: {
            color: {
                canvas: { $value: '#0f172a' },
                accent: { $value: '#14b8a6' },
                label: { $value: '{color.accent}' },
            },
        },
        root: {
            kind: 'view',
            sourceId: 'root',
            properties: {
                layout: { display: 'flex', direction: 'column', gap: 8, height: 300, width: 400 },
                paint: { backgroundColor: '{color.canvas}' },
            },
            children: [
                {
                    kind: 'button', sourceId: 'create', text: 'Create',
                    properties: { paint: { backgroundColor: '{main.color.accent}' } },
                },
                {
                    kind: 'view', sourceId: 'card',
                    properties: { paint: { backgroundColor: '{color.accent}' } },
                },
                {
                    kind: 'text', sourceId: 'title', text: 'Board',
                    properties: { text: { color: '{color.label}' } },
                },
                {
                    kind: 'view', sourceId: 'literal',
                    properties: { paint: { backgroundColor: '#123456' } },
                },
            ],
        },
    };
}

function renderTree(document, options = {}) {
    const app = createApp({
        id: 'token-app',
        stateVersion: '1',
        initialState: {},
        render: () => Design({ document, ...options }),
    });
    return JSON.parse(mount(app).renderJSON()).tree;
}

function flatten(node, output = []) {
    output.push(node);
    for (const child of node.children) flatten(child, output);
    return output;
}

test('Design emits each token receipt once and references it from every node', () => {
    const document = normalizeImport(tokenSource());
    const tree = renderTree(document);
    const paths = tree.tokens.map((receipt) => receipt.path).sort();
    assert.deepEqual(paths, ['main.color.accent', 'main.color.canvas', 'main.color.label']);
    assert.equal(new Set(paths).size, tree.tokens.length);
    const named = (suffix) => flatten(tree).find((node) => node.id.endsWith(suffix));
    const accent = tree.tokens.findIndex((receipt) => receipt.path === 'main.color.accent');
    const canvas = tree.tokens.findIndex((receipt) => receipt.path === 'main.color.canvas');
    assert.deepEqual(tree.tokenRefs, { backgroundColor: canvas });
    assert.deepEqual(named('/create').tokenRefs, { backgroundColor: accent });
    assert.deepEqual(named('/card').tokenRefs, { backgroundColor: accent });
    assert.equal(named('/create').style.backgroundColor, tree.tokens[accent].value);
    assert.equal(named('/literal').tokenRefs, undefined);
    assert.equal(named('/literal').style.backgroundColor, '#123456');
});

test('a token reached through another token keeps the path the node names', () => {
    const tree = renderTree(normalizeImport(tokenSource()));
    const title = flatten(tree).find((node) => node.id.endsWith('/title'));
    assert.equal(title.style.color, '#14b8a6');
    assert.deepEqual(tree.tokens[title.tokenRefs.color], {
        layer: 'primitive', path: 'main.color.label', value: '#14b8a6',
    });
});

test('a theme layer re-resolves receipts without moving any reference', () => {
    const document = normalizeImport(tokenSource());
    const light = renderTree(document);
    const layers = resolveTokenLayers(document, {
        themeOverrides: { 'main.color.canvas': '#f8fafc' },
    });
    const dark = renderTree(document, { tokenLayers: layers });
    assert.deepEqual(layers.diagnostics, []);
    assert.equal(dark.style.backgroundColor, '#f8fafc');
    const canvas = dark.tokens.findIndex((receipt) => receipt.path === 'main.color.canvas');
    assert.deepEqual(dark.tokens[canvas], {
        layer: 'theme', path: 'main.color.canvas', value: '#f8fafc',
    });
    assert.deepEqual(
        dark.tokens.filter((_, index) => index !== canvas),
        light.tokens.filter((_, index) => index !== canvas));
    const refs = (tree) => flatten(tree).map((node) => [node.id, node.tokenRefs]);
    assert.deepEqual(refs(dark), refs(light));
});

test('an alias reports the layer that supplied its value', () => {
    const document = normalizeImport(tokenSource());
    const layers = resolveTokenLayers(document, {
        themeOverrides: { 'main.color.accent': '#ff0000' },
    });
    const tree = renderTree(document, { tokenLayers: layers });
    const title = flatten(tree).find((node) => node.id.endsWith('/title'));
    assert.deepEqual(tree.tokens[title.tokenRefs.color], {
        layer: 'theme', path: 'main.color.label', value: '#ff0000',
    });
});

test('the runtime interns short receipts in long form', () => {
    const render = (receipts) => {
        const app = createApp({
            id: 'token-short',
            stateVersion: '1',
            initialState: {},
            render: () => jsx(View, { id: 'root', style: { width: 10, height: 10 }, children:
                receipts.map((receipt, index) => jsx(View, {
                    id: `n${index}`,
                    style: { backgroundColor: '#ffffff' },
                    tokenRefs: { backgroundColor: receipt },
                })) }),
        });
        return JSON.parse(mount(app).renderJSON()).tree;
    };
    const short = { layer: 'primitive', path: 'a.b', value: '#fff' };
    const tree = render([short, { ...short, value: '#ffffff' }]);
    assert.deepEqual(tree.tokens, [{ layer: 'primitive', path: 'a.b', value: '#ffffff' }]);
});

test('the runtime refuses token references that disagree with the node', () => {
    const receipt = { layer: 'primitive', path: 'a.b', value: '#112233' };
    const render = (props, extra = []) => {
        const app = createApp({
            id: 'token-refusal',
            stateVersion: '1',
            initialState: {},
            render: () => jsx(View, { id: 'root', style: { width: 10, height: 10 }, children: [
                jsx(View, { id: 'one', ...props }), ...extra] }),
        });
        return mount(app).renderJSON();
    };
    assert.match(JSON.parse(render({
        style: { backgroundColor: '#112233' }, tokenRefs: { backgroundColor: receipt },
    })).tree.tokens[0].path, /a\.b/);
    assert.throws(() => render({
        style: { backgroundColor: '#445566' }, tokenRefs: { backgroundColor: receipt },
    }), /disagrees with style.backgroundColor/);
    assert.throws(() => render({
        style: { backgroundColor: '#112233' }, tokenRefs: { fill: receipt },
    }), /not a token slot/);
    assert.throws(() => render({
        style: { backgroundColor: '#112233' },
        tokenRefs: { backgroundColor: { ...receipt, value: 'red' } },
    }), /path, a layer and a colour value/);
    assert.throws(() => render({
        style: { backgroundColor: '#112233' }, tokenRefs: { backgroundColor: receipt },
    }, [jsx(View, {
        id: 'two',
        style: { backgroundColor: '#445566' },
        tokenRefs: { backgroundColor: { ...receipt, value: '#445566' } },
    })]), /two different receipts/);
});

function slotDocument() {
    const color = (value) => ({ $type: 'color', $value: value });
    return {
        tokens: {
            'c.fill': color('#1e293b'),
            'c.text': color('#e5e7eb'),
            'c.run': color('#f472b6'),
            'c.shadow': color('#00000099'),
            'c.stopA': color('#6366f1'),
            'c.stopB': color('#ec4899'),
            'c.stroke': color('#f59e0b'),
            'c.short': color('#fff'),
        },
        root: {
            id: 'root', kind: 'view',
            properties: { layout: { width: 300, height: 200 } },
            children: [
                {
                    id: 'card', kind: 'view', children: [],
                    properties: {
                        layout: { width: 80, height: 60 },
                        paint: {
                            backgroundColor: '{c.fill}',
                            boxShadow: { offsetX: 2, offsetY: 3, blurRadius: 6, color: '{c.shadow}' },
                            backgroundLinearGradient: {
                                angle: 90,
                                stops: [
                                    { position: 0, color: '{c.stopA}' },
                                    { position: 1, color: '{c.stopB}' },
                                ],
                            },
                        },
                    },
                },
                {
                    id: 'label', kind: 'text', properties: { text: { color: '{c.text}' } },
                    children: [{
                        id: 'label/run', kind: 'text-run', text: 'Hi', children: [],
                        properties: { text: { color: '{c.run}' } },
                    }],
                },
                {
                    id: 'zig', kind: 'path', children: [],
                    properties: {
                        path: { d: 'M0 0 L20 10 L0 20 Z' },
                        paint: { fill: '{c.fill}', stroke: '{c.stroke}', strokeWidth: 3 },
                    },
                },
                {
                    id: 'short', kind: 'view', children: [],
                    properties: { paint: { backgroundColor: '{c.short}' } },
                },
            ],
        },
    };
}

function receiptOf(tree, node, slot) {
    return tree.tokens[node.tokenRefs[slot]];
}

test('Design refuses path fields it would otherwise drop', () => {
    const document = (paint, path = { d: 'M0 0 L20 10 L0 20 Z' }) => ({
        tokens: { 'c.fill': { $type: 'color', $value: '#14b8a6' } },
        root: {
            id: 'root', kind: 'view', properties: { layout: { width: 40, height: 40 } },
            children: [{
                id: 'zig', kind: 'path', children: [],
                properties: { path, paint: { fill: '{c.fill}', ...paint } },
            }],
        },
    });
    assert.doesNotThrow(() => materializeDesign(document({ strokeWidth: 2 })),
        'control: a supported path paint field is accepted');
    assert.throws(() => materializeDesign(document({ borderRadius: 4 })),
        /design path zig paint\.borderRadius is not supported/);
    assert.throws(() => materializeDesign(document({ strokeOpacity: 0.5 })),
        /design path zig paint\.strokeOpacity is not supported/);
    const stopped = (stop) => ({
        tokens: { 'c.fill': { $type: 'color', $value: '#14b8a6' } },
        root: {
            id: 'root', kind: 'view', properties: { layout: { width: 40, height: 40 }, paint: {
                backgroundLinearGradient: {
                    angle: 90,
                    stops: [{ position: 0, color: '{c.fill}', ...stop },
                        { position: 1, color: '#ffffff' }],
                },
            } },
            children: [],
        },
    });
    assert.doesNotThrow(() => materializeDesign(stopped({})),
        'control: a stop with a position and a colour is accepted');
    assert.throws(() => materializeDesign(stopped({ opacity: 0.5 })),
        /backgroundLinearGradient\.stops\.0\.opacity is not supported/);
    assert.throws(() => materializeDesign(document({}, { d: 'M0 0', winding: 'x' })),
        /design path zig path\.winding is not supported/);
});

test('Design produces a receipt for every token slot', () => {
    const tree = renderTree(slotDocument());
    const nodes = flatten(tree);
    const card = nodes.find((node) => node.id === 'card');
    assert.deepEqual(Object.keys(card.tokenRefs).sort(), [
        'backgroundColor', 'backgroundLinearGradient.stops.0',
        'backgroundLinearGradient.stops.1', 'boxShadow',
    ]);
    assert.equal(receiptOf(tree, card, 'boxShadow').path, 'c.shadow');
    assert.equal(card.style.boxShadow.color, '#00000099');
    assert.equal(receiptOf(tree, card, 'backgroundLinearGradient.stops.1').path, 'c.stopB');
    assert.equal(card.style.backgroundLinearGradient.stops[1].color, '#ec4899');
    const label = nodes.find((node) => node.id === 'label');
    assert.equal(receiptOf(tree, label, 'color').path, 'c.text');
    const run = label.children.find((node) => node.type === 'text-run');
    assert.equal(run.text, 'Hi');
    assert.equal(receiptOf(tree, run, 'color').path, 'c.run');
    const zig = nodes.find((node) => node.id === 'zig');
    assert.equal(zig.type, 'path');
    assert.equal(zig.d, 'M0 0 L20 10 L0 20 Z');
    assert.equal(receiptOf(tree, zig, 'stroke').path, 'c.stroke');
    assert.equal(receiptOf(tree, zig, 'fill').path, 'c.fill');
    assert.equal(zig.stroke, '#f59e0b');
    assert.equal(tree.tokens.filter((receipt) => receipt.path === 'c.fill').length, 1);
});

test('Design keeps every supported gradient field and refuses unknown ones', () => {
    const gradient = {
        kind: 'two-circle-radial', spread: 'reflect', interpolation: 'oklab',
        colorSpace: 'display-p3', centerX: 0.25, centerY: 0.75, radiusX: 0.4, radiusY: 0.6,
        focalX: 0.3, focalY: 0.2, startRadius: 0.1, endRadius: 0.9,
        stops: [{ position: 0, color: '{c.stopA}' }, { position: 1, color: '#000000' }],
    };
    const document = slotDocument();
    const card = document.root.children.find((node) => node.id === 'card');
    card.properties.paint.backgroundLinearGradient = gradient;
    const zig = document.root.children.find((node) => node.id === 'zig');
    zig.properties.paint.fill = { kind: 'conic', angle: 45, centerX: 0.5, centerY: 0.5,
        stops: gradient.stops };
    const nodes = flatten(renderTree(document));
    const { stops, ...fields } = nodes.find((node) => node.id === 'card')
        .style.backgroundLinearGradient;
    const { stops: _ignored, ...expected } = gradient;
    assert.deepEqual(fields, expected);
    assert.equal(stops[0].color, '#6366f1');
    const fill = nodes.find((node) => node.id === 'zig').fill;
    assert.equal(fill.kind, 'conic');
    assert.equal(fill.angle, 45);
    assert.equal(fill.centerX, 0.5);
    card.properties.paint.backgroundLinearGradient = { ...gradient, wobble: 1 };
    assert.throws(() => renderTree(document), /backgroundLinearGradient\.wobble/);
});

test('Design forwards every Path stroke attribute', () => {
    const document = slotDocument();
    const zig = document.root.children.find((node) => node.id === 'zig');
    Object.assign(zig.properties.paint, {
        strokeLineJoin: 'round', strokeLineCap: 'square', strokeMiterLimit: 6,
        strokeDasharray: [4, 2], strokeDashoffset: 1.5,
    });
    const path = flatten(renderTree(document)).find((node) => node.id === 'zig');
    assert.equal(path.strokeWidth, 3);
    assert.equal(path.strokeLineJoin, 'round');
    assert.equal(path.strokeLineCap, 'square');
    assert.equal(path.strokeMiterLimit, 6);
    assert.deepEqual(path.strokeDasharray, [4, 2]);
    assert.notEqual(path.strokeDasharray, zig.properties.paint.strokeDasharray);
    assert.equal(path.strokeDashoffset, 1.5);
});

test('a short hex token colour gets a receipt in its canonical long form', () => {
    const tree = renderTree(slotDocument());
    const short = flatten(tree).find((node) => node.id === 'short');
    assert.equal(short.style.backgroundColor, '#ffffff');
    assert.deepEqual(receiptOf(tree, short, 'backgroundColor'),
        { layer: 'primitive', path: 'c.short', value: '#ffffff' });
    const themed = renderTree(slotDocument(), { tokenLayers: { theme: { 'c.short': '#0008' } } });
    const dark = flatten(themed).find((node) => node.id === 'short');
    assert.equal(dark.style.backgroundColor, '#00000088');
    assert.deepEqual(receiptOf(themed, dark, 'backgroundColor'),
        { layer: 'theme', path: 'c.short', value: '#00000088' });
});

test('a theme switch moves the receipt and the colour of every slot', () => {
    const light = renderTree(slotDocument());
    const theme = {
        'c.fill': '#0f172a', 'c.text': '#111827', 'c.run': '#22c55e', 'c.shadow': '#ff000080',
        'c.stopA': '#f97316', 'c.stopB': '#14b8a6', 'c.stroke': '#3b82f6', 'c.short': '#000',
    };
    const dark = renderTree(slotDocument(), { tokenLayers: { theme } });
    const refs = (tree) => flatten(tree).map((node) => [node.type, node.tokenRefs]);
    assert.deepEqual(refs(dark), refs(light));
    assert.deepEqual(dark.tokens.map((receipt) => receipt.path),
        light.tokens.map((receipt) => receipt.path));
    for (const [index, receipt] of dark.tokens.entries()) {
        assert.equal(receipt.layer, 'theme');
        assert.notEqual(receipt.value, light.tokens[index].value, receipt.path);
    }
    const nodes = flatten(dark);
    const card = nodes.find((node) => node.id === 'card');
    assert.equal(card.style.boxShadow.color, '#ff000080');
    assert.equal(card.style.backgroundLinearGradient.stops[0].color, '#f97316');
    assert.equal(nodes.find((node) => node.type === 'text-run').style.color, '#22c55e');
    assert.equal(nodes.find((node) => node.id === 'zig').stroke, '#3b82f6');
});

test('the runtime refuses token slots an element does not have', () => {
    const receipt = { layer: 'primitive', path: 'a.b', value: '#112233' };
    const render = (element) => mount(createApp({
        id: 'token-slots',
        stateVersion: '1',
        initialState: {},
        render: () => jsx(View, { id: 'root', style: { width: 10, height: 10 }, children: [element] }),
    })).renderJSON();
    assert.throws(() => render(jsx(View, {
        id: 'v', style: { backgroundColor: '#112233' }, tokenRefs: { stroke: receipt },
    })), /not a token slot of view/);
    assert.throws(() => render(jsx(Path, {
        id: 'p', d: 'M0 0 L1 1', stroke: '#112233', tokenRefs: { backgroundColor: receipt },
    })), /not a token slot of path/);
    assert.throws(() => render(jsx(View, {
        id: 'g',
        style: { backgroundLinearGradient: { angle: 0, stops: [
            { position: 0, color: '#112233' }, { position: 1, color: '#445566' }] } },
        tokenRefs: { 'backgroundLinearGradient.stops.1': receipt },
    })), /disagrees with style.backgroundLinearGradient.stops.1/);
    assert.throws(() => render(jsx(Text, { id: 't', children: [
        jsx(TextRun, { style: { color: '#445566' }, tokenRefs: { color: receipt }, text: 'x' }),
    ] })), /disagrees with style.color/);
    const short = JSON.parse(render(jsx(View, {
        id: 's', style: { backgroundColor: '#123' },
        tokenRefs: { backgroundColor: { ...receipt, value: '#112233' } },
    }))).tree;
    assert.equal(short.tokens[0].value, '#112233');
    const stroke = JSON.parse(render(jsx(Path, {
        id: 'p', d: 'M0 0 L1 1', stroke: '#112233', tokenRefs: { stroke: receipt },
    }))).tree;
    assert.deepEqual(stroke.children[0].tokenRefs, { stroke: 0 });
});

function compositingDesign(children, rootCompositing) {
    return normalizeImport({
        source: {
            key: 'screen',
            adapter: 'fixture',
            adapterVersion: '1',
            formatVersion: '1',
            revision: 'compositing',
        },
        root: {
            kind: 'view',
            sourceId: 'root',
            properties: { layout: { width: 120, height: 80 } },
            ...(rootCompositing ? { compositing: rootCompositing } : {}),
            children,
        },
    });
}

function designNode(sourceId, compositing, children = []) {
    return {
        kind: 'view',
        sourceId,
        properties: { layout: { width: 20, height: 20 } },
        ...(compositing ? { compositing } : {}),
        children,
    };
}

function renderedTree(document) {
    const app = createApp({
        id: 'compositing-design',
        stateVersion: '1',
        initialState: {},
        actions: {},
        render: () => materializeDesign(document),
    });
    return JSON.parse(mount(app).renderJSON()).tree;
}

test('DesignIR compositing lowers into the retained styles the renderer reads', () => {
    const document = compositingDesign([
        designNode('masked', undefined, [
            designNode('content', {
                blendMode: 'color_dodge', opacity: 0.25, isolate: true, maskRef: 'screen/cutout',
            }),
            designNode('cutout', { mask: 'luminance' }),
        ]),
        designNode('group', { blendMode: 'multiply' }, [designNode('inner', { opacity: 0.5 })]),
    ]);
    assert.equal(document.schemaVersion, 2);
    const tree = renderedTree(document);
    const [masked, group] = tree.children;
    const [content, cutout] = masked.children;
    assert.deepEqual([content.id, cutout.id, group.id],
        ['screen/content', 'screen/cutout', 'screen/group']);
    assert.deepEqual(
        { ...content.style },
        { height: 20, isolation: 'isolate', mixBlendMode: 'color_dodge', opacity: 0.25, width: 20 },
    );
    assert.equal(cutout.style.maskMode, 'luminance');
    assert.equal(cutout.style.mixBlendMode, undefined);
    assert.equal(group.style.mixBlendMode, 'multiply');
    assert.equal(group.children[0].style.opacity, 0.5);

    for (const mode of BLEND_MODES) {
        const tree = renderedTree(compositingDesign([designNode('blended', { blendMode: mode })]));
        assert.equal(tree.children[0].style.mixBlendMode, mode === 'normal' ? undefined : mode, mode);
    }
    // Negative control: the same document with its compositing stripped
    // carries none of these styles.
    const plain = structuredClone(document);
    plain.schemaVersion = 1;
    const strip = (node) => {
        delete node.compositing;
        node.children.forEach(strip);
    };
    strip(plain.root);
    const visit = (node) => {
        for (const key of ['mixBlendMode', 'opacity', 'isolation', 'maskMode']) {
            assert.equal(node.style?.[key], undefined, `${node.id} ${key}`);
        }
        node.children.forEach(visit);
    };
    visit(renderedTree(plain));
});

test('DesignIR materialization validates the schema version of compositing', () => {
    const document = compositingDesign([designNode('blended', { blendMode: 'multiply' })]);
    const older = structuredClone(document);
    older.schemaVersion = 1;
    assert.throws(() => materializeDesign(older),
        /design node screen\/blended compositing needs schemaVersion 2/);
    const unversioned = structuredClone(document);
    delete unversioned.schemaVersion;
    assert.throws(() => materializeDesign(unversioned), /needs schemaVersion 2/);
    const newer = structuredClone(document);
    for (const version of [2, 3, 4]) {
        newer.schemaVersion = version;
        assert.equal(renderedTree(newer).children[0].style.mixBlendMode, 'multiply');
        newer.root.children[0].compositing.mask = 'alpha';
        assert.throws(() => materializeDesign(newer), /a mask node takes no blend mode but normal/);
        delete newer.root.children[0].compositing.mask;
    }
    newer.schemaVersion = 5;
    assert.throws(() => materializeDesign(newer), /unsupported DesignIR schemaVersion: 5/);
    const plain = compositingDesign([designNode('plain')]);
    assert.equal(plain.schemaVersion, 1);
    assert.doesNotThrow(() => materializeDesign(plain));
    assert.doesNotThrow(() => materializeDesign(fixture()), 'a document without a version');

    const bad = (compositing, pattern) => {
        const copy = structuredClone(document);
        copy.root.children[0].compositing = compositing;
        assert.throws(() => materializeDesign(copy), pattern);
    };
    bad({ blendMode: 'spectral' }, /blendMode must be a known blend mode/);
    bad({ blendMode: 'color-dodge' }, /blendMode must be a known blend mode/);
    bad({ opacity: 2 }, /opacity must be a number from 0 to 1/);
    bad({ isolate: 'yes' }, /isolate must be a boolean/);
    bad({ mask: 'inverted' }, /mask must be alpha or luminance/);
    bad({ futureBlend: 'x' }, /unknown field 'futureBlend'/);
});

test('DesignIR mask references resolve to the one sibling mask or are refused', () => {
    const refuses = (children, pattern, rootCompositing) => {
        const document = normalizeImport({
            source: {
                key: 'screen', adapter: 'fixture', adapterVersion: '1', formatVersion: '1',
                revision: 'masks',
            },
            root: { kind: 'view', sourceId: 'root', children },
        });
        if (rootCompositing) document.root.compositing = rootCompositing;
        if (rootCompositing) document.schemaVersion = 2;
        assert.throws(() => materializeDesign(document), pattern);
    };
    refuses([
        designNode('content'),
        designNode('cutout', { mask: 'alpha' }),
    ], /screen\/content is not masked by screen\/cutout, but the renderer masks every sibling/);
    refuses([
        designNode('content', { maskRef: 'screen/cutout' }),
        designNode('cutout', { mask: 'alpha' }),
        designNode('second', { mask: 'luminance' }),
    ], /has 2 mask children/);
    refuses([designNode('alone')], /the root has no siblings/, { mask: 'alpha' });
    refuses([designNode('alone')], /the root has no siblings/, { maskRef: 'screen/x' });

    const document = compositingDesign([
        designNode('content', { maskRef: 'screen/cutout' }),
        designNode('cutout', { mask: 'alpha' }),
    ]);
    const dangling = structuredClone(document);
    dangling.root.children[0].compositing.maskRef = 'screen/missing';
    assert.throws(() => materializeDesign(dangling),
        /maskRef 'screen\/missing' does not name a sibling mask/);
    const blendedMask = structuredClone(document);
    blendedMask.root.children[1].compositing.blendMode = 'multiply';
    assert.throws(() => materializeDesign(blendedMask), /a mask node takes no blend mode but normal/);
    assert.doesNotThrow(() => materializeDesign(document));
});

test('rendered nodes carry the design identity they came from as an anchor', async () => {
    const sourceBytes = await readFile(join(fixtures, 'pulp-emitter-generic.export.json'));
    const document = normalizeImport(decodeFigmaPluginExport(
        JSON.parse(sourceBytes.toString('utf8')),
        {
            sourceHash: `sha256:${createHash('sha256').update(sourceBytes).digest('hex')}`,
            sourceKey: 'main',
        },
    ));
    const app = createApp({
        id: 'anchor-proof',
        stateVersion: '1',
        initialState: {},
        actions: { tap: () => ({}) },
        render: () => materializeDesign(document, {
            actions: { 'main/1:2': { press: 'tap' } },
            viewport: { height: 240, padding: 20, width: 420 },
        }),
    });
    const tree = JSON.parse(mount(app).renderJSON()).tree;
    const nodes = [];
    const walk = (node) => { nodes.push(node); node.children.forEach(walk); };
    walk(tree);
    const interactive = nodes.filter((node) => node.events !== undefined);
    assert.ok(interactive.length > 0);
    for (const node of interactive) assert.equal(node.anchor, 'figma:1:2');
    for (const node of nodes) assert.match(node.anchor, /^figma:/);
});


test('refuses unsupported tracks and materials throughout the design tree', () => {
    for (const version of [3, 4]) {
        for (const location of ['root', 'child', 'hidden-descendant']) {
            for (const field of ['tracks', 'material']) {
                if (version === 3 && field === 'tracks') continue;
                const document = fixture();
                document.schemaVersion = version;
                let node = document.root;
                if (location !== 'root') node = node.children[0];
                if (location === 'hidden-descendant') {
                    node.properties.layout = { display: 'none' };
                    node.children = [{ id: 'main/nested', kind: 'view', children: [] }];
                    node = node.children[0];
                }
                node[field] = field === 'tracks'
                    ? [{ property: 'opacity', keyframes: [{ time: 0, value: 0 }, { time: 1, value: 1 }] }]
                    : { id: 'example.material', values: {} };
                const expected = new RegExp(`design node ${node.id}.*unsupported ${field}`);
                assert.throws(() => materializeDesign(document), expected, `${version} ${location} ${field}`);
                assert.throws(() => Design({ document }), expected);
                delete node[field];
                assert.doesNotThrow(() => materializeDesign(document));
            }
        }
    }
    const empty = fixture();
    empty.schemaVersion = 4;
    empty.root.tracks = [];
    assert.doesNotThrow(() => materializeDesign(empty));
});
