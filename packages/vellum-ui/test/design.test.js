import assert from 'node:assert/strict';
import test from 'node:test';

import { createApp, materializeDesign, mount } from '../src/index.js';

const protocol = 'vellum.authoring-host.v1';

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
    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({
        protocol, action: tree.children[0].events.press,
    }))).tree;
    assert.equal(tree.children[0].id, 'main/create');
    assert.equal(JSON.parse(bridge.snapshotStateJSON()).state.model.presses, 1);
});

test('fails closed on unresolved tokens, duplicate ids, and unsupported kinds', () => {
    const unresolved = fixture();
    unresolved.root.properties.paint.backgroundColor = '{missing.token}';
    assert.throws(() => materializeDesign(unresolved), /unresolved design token/);

    const duplicate = fixture();
    duplicate.root.children.push({ ...duplicate.root.children[0] });
    assert.throws(() => materializeDesign(duplicate), /duplicate materialized design id/);

    const unsupported = fixture();
    unsupported.root.children[0].kind = 'video';
    assert.throws(() => materializeDesign(unsupported), /unsupported materialized design node kind/);
});
