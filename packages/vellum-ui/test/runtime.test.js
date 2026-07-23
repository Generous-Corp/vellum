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
        action: tree.events.press,
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
    assert.throws(() => createApp({
        initialState: cyclic,
        render: () => jsx(View, { id: 'cyclic' }),
    }), /contains a cycle/);

    assert.throws(() => createApp({
        initialState: { callback() {} },
        render: () => jsx(View, { id: 'function' }),
    }), /not JSON-serializable/);
});

test('rejects duplicate stable IDs and changed hook order', () => {
    const duplicate = mount(() => jsx(View, {
        id: 'root',
        children: [
            jsx(Text, { id: 'same', children: 'A' }),
            jsx(Text, { id: 'same', children: 'B' }),
        ],
    }));
    assert.throws(() => duplicate.renderJSON(), /duplicate Vellum node id: same/);

    let includeHook = false;
    const conditional = mount(() => {
        if (includeHook) useState(1);
        return jsx(View, { id: 'conditional' });
    });
    conditional.renderJSON();
    includeHook = true;
    assert.throws(() => conditional.renderJSON(), /hook order changed/);

    let switchHook = false;
    const changedKind = mount(() => {
        if (switchHook) useMemo(() => 1, []);
        else useState(1);
        return jsx(View, { id: 'kind' });
    });
    changedKind.renderJSON();
    switchHook = true;
    assert.throws(() => changedKind.renderJSON(), /hook kind changed/);
});

test('preserves component-scoped state when keyed children reorder', () => {
    function Item({ id }) {
        const [label, setLabel] = useState(() => id.toUpperCase());
        return jsx(Button, {
            id,
            onPress: () => setLabel((value) => `${value}!`),
            children: label,
        });
    }
    let reversed = false;
    const bridge = mount(() => jsx(Stack, {
        id: 'items',
        children: (reversed ? ['b', 'a'] : ['a', 'b'])
            .map((id) => jsx(Item, { id }, id)),
    }));
    let tree = JSON.parse(bridge.renderJSON()).tree;
    const actionA = tree.children[0].events.press;
    bridge.dispatchJSON(JSON.stringify({ protocol, action: actionA }));
    reversed = true;
    tree = JSON.parse(bridge.renderJSON()).tree;
    assert.deepEqual(
        tree.children.map((item) => `${item.id}:${item.children[0].text}`),
        ['b:B', 'a:A!'],
    );
});

test('keeps named and inline serialized actions in disjoint namespaces', () => {
    function Application(model) {
        const [inlineCount, setInlineCount] = useState(0);
        return jsx(Stack, {
            id: 'actions',
            children: [
                jsx(Button, {
                    id: 'button',
                    onPress: () => setInlineCount((value) => value + 1),
                    children: `Inline ${inlineCount}`,
                }),
                jsx(Button, {
                    id: 'named',
                    onPress: 'button:press',
                    children: `Named ${model.namedCount}`,
                }),
            ],
        });
    }
    const bridge = mount(createApp({
        id: 'action-namespaces',
        initialState: { namedCount: 0 },
        actions: {
            'button:press': (model) => ({ namedCount: model.namedCount + 1 }),
        },
        render: Application,
    }));
    let tree = JSON.parse(bridge.renderJSON()).tree;
    assert.match(tree.children[0].events.press, /^inline:/);
    assert.equal(tree.children[1].events.press, 'named:button:press');
    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({
        protocol, action: tree.children[1].events.press,
    }))).tree;
    assert.equal(tree.children[0].children[0].text, 'Inline 0');
    assert.equal(tree.children[1].children[0].text, 'Named 1');
});

test('failed renders and incompatible restores do not corrupt committed state or handlers', () => {
    let failRender = false;
    function Application() {
        const [count, setCount] = useState(0);
        const increment = failRender ? 100 : 1;
        const button = jsx(Button, {
            id: 'increment',
            onPress: () => setCount((value) => value + increment),
            children: `Count ${count}`,
        });
        if (!failRender) return button;
        return jsx(Stack, {
            id: 'broken',
            children: [
                button,
                jsx(Text, { id: 'duplicate', children: 'A' }),
                jsx(Text, { id: 'duplicate', children: 'B' }),
            ],
        });
    }
    const bridge = mount(createApp({ id: 'transactional-app', render: Application }));
    let tree = JSON.parse(bridge.renderJSON()).tree;
    const action = tree.events.press;
    const snapshot = JSON.parse(bridge.snapshotStateJSON());

    failRender = true;
    assert.throws(() => bridge.renderJSON(), /duplicate Vellum node id/);
    failRender = false;

    const stateFrame = snapshot.state.frames.find((frame) => frame.values.length > 0);
    stateFrame.values = [];
    assert.throws(
        () => bridge.restoreStateJSON(JSON.stringify(snapshot)),
        /hook layout does not match/,
    );

    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({ protocol, action }))).tree;
    assert.equal(tree.children[0].text, 'Count 1');
});

test('snapshots exclude memo caches and recompute them after restore', () => {
    let computations = 0;
    function Application() {
        const [count, setCount] = useState(0);
        const derived = useMemo(() => {
            computations += 1;
            return () => count;
        }, [count]);
        return jsx(Button, {
            id: 'memo',
            onPress: () => setCount((value) => value + 1),
            children: `Value ${derived()}`,
        });
    }
    const bridge = mount(createApp({ id: 'memo-app', render: Application }));
    let tree = JSON.parse(bridge.renderJSON()).tree;
    const snapshot = bridge.snapshotStateJSON();
    assert.equal(computations, 1);
    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({
        protocol, action: tree.events.press,
    }))).tree;
    assert.equal(tree.children[0].text, 'Value 1');
    assert.equal(computations, 2);
    tree = JSON.parse(bridge.restoreStateJSON(snapshot)).tree;
    assert.equal(tree.children[0].text, 'Value 0');
    assert.equal(computations, 3);
});

test('rejects lossy property semantics without invoking getters', () => {
    let getterCalls = 0;
    const accessor = {};
    Object.defineProperty(accessor, 'value', {
        enumerable: true,
        get() {
            getterCalls += 1;
            return 42;
        },
    });
    assert.throws(() => createApp({
        initialState: accessor,
        render: () => jsx(View, { id: 'accessor' }),
    }), /unsupported property semantics/);
    assert.equal(getterCalls, 0);

    const symbolState = { okay: true };
    symbolState[Symbol('hidden')] = 1;
    assert.throws(() => createApp({
        initialState: symbolState,
        render: () => jsx(View, { id: 'symbol' }),
    }), /symbol properties/);

    const sparse = [];
    sparse.length = 2;
    sparse[1] = 'present';
    assert.throws(() => createApp({
        initialState: sparse,
        render: () => jsx(View, { id: 'sparse' }),
    }), /dense array/);
});

test('uses injective identities for actions and generated retained nodes', () => {
    function Application() {
        const [selected, setSelected] = useState('none');
        return jsx(Stack, {
            id: 'identity-root',
            children: [
                jsx(Button, { id: ':', onPress: () => setSelected('colon'), children: ':' }),
                jsx(Button, {
                    id: '_3A',
                    onPress: () => setSelected('literal'),
                    children: `_3A ${selected}`,
                }),
            ],
        });
    }
    const bridge = mount(Application);
    let tree = JSON.parse(bridge.renderJSON()).tree;
    const firstAction = tree.children[0].events.press;
    const secondAction = tree.children[1].events.press;
    assert.notEqual(firstAction, secondAction);
    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({
        protocol, action: firstAction,
    }))).tree;
    assert.equal(tree.children[1].children[0].text, '_3A colon');

    const duplicateKeys = mount(() => jsx('view', {
        id: 'key-root',
        children: [
            jsx('view', { children: 'A' }, 'same'),
            jsx('view', { children: 'B' }, 'same'),
        ],
    }));
    assert.throws(() => duplicateKeys.renderJSON(), /duplicate Vellum node id/);
});

test('restores snapshots on fresh mounts and across conditional navigation', () => {
    function ScreenA() {
        const [edits, setEdits] = useState(0);
        return jsx(Button, {
            id: 'edit-a',
            onPress: () => setEdits((value) => value + 1),
            children: `A ${edits}`,
        });
    }
    function ScreenB() {
        return jsx(Text, { id: 'screen-b', children: 'B' });
    }
    function Application() {
        const [screen, setScreen] = useState('a');
        return jsx(Stack, {
            id: 'navigation',
            children: [
                jsx(Button, {
                    id: 'navigate',
                    onPress: () => setScreen((value) => value === 'a' ? 'b' : 'a'),
                    children: screen,
                }),
                screen === 'a' ? jsx(ScreenA, {}, 'a') : jsx(ScreenB, {}, 'b'),
            ],
        });
    }
    const first = mount(createApp({ id: 'navigation-app', render: Application }));
    let tree = JSON.parse(first.renderJSON()).tree;
    tree = JSON.parse(first.dispatchJSON(JSON.stringify({
        protocol, action: tree.children[1].events.press,
    }))).tree;
    assert.equal(tree.children[1].children[0].text, 'A 1');
    const snapshot = first.snapshotStateJSON();
    tree = JSON.parse(first.dispatchJSON(JSON.stringify({
        protocol, action: tree.children[0].events.press,
    }))).tree;
    assert.equal(tree.children[1].id, 'screen-b');
    tree = JSON.parse(first.restoreStateJSON(snapshot)).tree;
    assert.equal(tree.children[1].children[0].text, 'A 1');

    const fresh = mount(createApp({ id: 'navigation-app', render: Application }));
    tree = JSON.parse(fresh.restoreStateJSON(snapshot)).tree;
    assert.equal(tree.children[1].children[0].text, 'A 1');
});

test('validates every snapshot state slot exactly once', () => {
    function Application() {
        const [first] = useState('A');
        const [second] = useState('B');
        return jsx(Text, { id: 'slots', children: `${first}/${second}` });
    }
    const bridge = mount(createApp({ id: 'slot-app', render: Application }));
    bridge.renderJSON();
    const snapshot = JSON.parse(bridge.snapshotStateJSON());
    const frame = snapshot.state.frames.find((entry) => entry.values.length === 2);
    frame.values[1].slot = frame.values[0].slot;
    assert.throws(
        () => bridge.restoreStateJSON(JSON.stringify(snapshot)),
        /hook slot does not match/,
    );
});

test('distinguishes different component functions that share a display name', () => {
    function Left() {
        const [value, setValue] = useState('LEFT');
        return jsx(Button, {
            id: 'shared',
            onPress: () => setValue((prior) => `${prior}!`),
            children: value,
        });
    }
    function Right() {
        const [value] = useState('RIGHT');
        return jsx(Button, { id: 'shared', onPress() {}, children: value });
    }
    Left.displayName = 'Shared';
    Right.displayName = 'Shared';
    let right = false;
    const bridge = mount(() => jsx(right ? Right : Left, {}));
    let tree = JSON.parse(bridge.renderJSON()).tree;
    tree = JSON.parse(bridge.dispatchJSON(JSON.stringify({
        protocol, action: tree.events.press,
    }))).tree;
    assert.equal(tree.children[0].text, 'LEFT!');
    right = true;
    tree = JSON.parse(bridge.renderJSON()).tree;
    assert.equal(tree.children[0].text, 'RIGHT');
});

test('rejects style accessors without invocation and safely preserves __proto__', () => {
    let getterCalls = 0;
    const accessorStyle = {};
    Object.defineProperty(accessorStyle, 'width', {
        enumerable: true,
        get() {
            getterCalls += 1;
            return 10;
        },
    });
    const bad = mount(() => jsx(View, { id: 'bad-style', style: accessorStyle }));
    assert.throws(() => bad.renderJSON(), /unsupported property semantics/);
    assert.equal(getterCalls, 0);

    const safeStyle = { width: 10 };
    Object.defineProperty(safeStyle, '__proto__', {
        enumerable: true,
        value: 'literal',
    });
    const bridge = mount(() => jsx(View, { id: 'safe-style', style: safeStyle }));
    const tree = JSON.parse(bridge.renderJSON()).tree;
    assert.equal(tree.style.__proto__, 'literal');
});
