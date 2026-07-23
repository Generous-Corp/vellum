import assert from 'node:assert/strict';
import test from 'node:test';

import {
    Button,
    Stack,
    Text,
    View,
    createApp,
    jsx,
    mount,
    useMemo,
    useState,
} from '../src/index.js';

const protocol = 'vellum.authoring-host.v1';

test('renders one deterministic serializable retained tree', () => {
    const bridge = mount(() => jsx(View, {
        id: 'screen',
        style: { width: 320, backgroundColor: '#101828' },
        children: jsx(Text, { id: 'title', children: ['Hello ', 7] }),
    }));
    const first = bridge.renderJSON();
    const second = bridge.renderJSON();
    assert.equal(first, second);
    const envelope = JSON.parse(first);
    assert.equal(envelope.protocol, protocol);
    assert.equal(envelope.tree.id, 'screen');
    assert.equal(envelope.tree.children[0].children[0].text, 'Hello ');
    assert.equal(envelope.tree.children[0].children[1].text, '7');
    assert.equal(JSON.stringify(envelope).includes('function'), false);
});

test('dispatches inline JSX behavior and persists hook state', () => {
    function Counter() {
        const [count, setCount] = useState(0);
        const label = useMemo(() => `Count ${count}`, [count]);
        return jsx(Stack, {
            id: 'counter',
            children: [
                jsx(Text, { id: 'count', children: label }),
                jsx(Button, {
                    id: 'increment',
                    onPress: () => setCount((value) => value + 1),
                    children: 'Increment',
                }),
            ],
        });
    }
    const bridge = mount(Counter);
    let tree = JSON.parse(bridge.renderJSON()).tree;
    assert.equal(tree.children[0].children[0].text, 'Count 0');
    const action = tree.children[1].events.press;
    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({
        protocol,
        action,
        payload: { pointerType: 'mouse' },
    }))).tree;
    assert.equal(tree.children[0].children[0].text, 'Count 1');

    const snapshot = bridge.snapshotStateJSON();
    bridge.dispatchJSON(JSON.stringify({ protocol, action, payload: null }));
    tree = JSON.parse(bridge.restoreStateJSON(snapshot)).tree;
    assert.equal(tree.children[0].children[0].text, 'Count 1');
});

test('supports named model actions for imported/generated components', () => {
    const app = createApp({
        initialState: { boards: 1 },
        actions: {
            addBoard(model) {
                return { boards: model.boards + 1 };
            },
        },
        render(model) {
            return jsx(Button, {
                id: 'add-board',
                onPress: 'addBoard',
                children: `Boards ${model.boards}`,
            });
        },
    });
    const bridge = mount(app);
    let tree = JSON.parse(bridge.renderJSON()).tree;
    assert.equal(tree.children[0].text, 'Boards 1');
    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({
        protocol,
        action: 'addBoard',
    }))).tree;
    assert.equal(tree.children[0].text, 'Boards 2');
});

test('requires explicit stable IDs on interactive nodes', () => {
    const bridge = mount(() => jsx(Button, { onPress() {}, children: 'No id' }));
    assert.throws(() => bridge.renderJSON(), /requires an explicit stable id/);
});

test('rejects non-serializable style and malformed host requests', () => {
    const badStyle = mount(() => jsx(View, { id: 'bad', style: { value: {} } }));
    assert.throws(() => badStyle.renderJSON(), /not serializable/);

    const bridge = mount(() => jsx(View, { id: 'okay' }));
    assert.throws(() => bridge.dispatchJSON('{}'), /invalid vellum.authoring-host.v1/);
});

test('rejects non-serializable and cyclic state instead of silently losing it', () => {
    const cyclic = {};
    cyclic.self = cyclic;
    const cyclicBridge = mount(createApp({
        initialState: cyclic,
        render: () => jsx(View, { id: 'cyclic' }),
    }));
    assert.throws(() => cyclicBridge.snapshotStateJSON(), /contains a cycle/);

    const functionBridge = mount(createApp({
        initialState: { callback() {} },
        render: () => jsx(View, { id: 'function' }),
    }));
    assert.throws(() => functionBridge.snapshotStateJSON(), /not JSON-serializable/);
});
