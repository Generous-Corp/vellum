import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { createApp, Design, materializeDesign, mount } from '../src/index.js';
import {
    BLEND_MODES,
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
    newer.schemaVersion = 3;
    assert.throws(() => materializeDesign(newer), /unsupported DesignIR schemaVersion: 3/);
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
