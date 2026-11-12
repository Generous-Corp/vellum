import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { createApp, Design, jsx, materializeDesign, mount, View } from '../src/index.js';
import {
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
