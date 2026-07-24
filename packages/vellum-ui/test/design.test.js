import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { createApp, Design, materializeDesign, mount } from '../src/index.js';
import {
    decodeFigmaPluginExport,
    normalizeImport,
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
