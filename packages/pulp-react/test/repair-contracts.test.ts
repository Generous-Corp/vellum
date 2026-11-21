import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PulpHostConfig } from '../src/host-config.js';
import { applyAllProps, applyChangedProps } from '../src/prop-applier.js';
import { applyTransformProp } from '../src/prop-applier-transform.js';
import * as widgets from '../src/intrinsics.js';
import { createElement } from 'react';
import { createRoot, render, unmount } from '../src/index.js';
import type { PulpContainer, PulpInstance } from '../src/types.js';

// Exercise host mutations with a stateful native tree, including native append
// semantics. Counting calls alone would miss a reorder that recreated widgets.
const host = PulpHostConfig as any;
let tree: Map<string, string[]>;
let captions: Map<string, string>;
let listeners: Map<string, (...args: unknown[]) => unknown>;
function remove(id: string) {
    for (const children of tree.values()) {
        const index = children.indexOf(id);
        if (index >= 0) children.splice(index, 1);
    }
}
function insert(parent: string, id: string, index?: number) {
    remove(id);
    const children = tree.get(parent) ?? [];
    children.splice(index ?? children.length, 0, id);
    tree.set(parent, children);
}
function instance(type: string, props: Record<string, unknown>, container: PulpContainer): PulpInstance {
    return host.createInstance(type, props, container, {}, {});
}
function update(node: PulpInstance, props: Record<string, unknown>) {
    host.commitUpdate(node, true, node.type, node.props, props, {});
}
beforeEach(() => {
    tree = new Map(); captions = new Map(); listeners = new Map();
    vi.stubGlobal('createLabel', (id: string, text: string, parent: string) => {
        insert(parent, id); captions.set(id, text);
    });
    vi.stubGlobal('createPanel', (id: string, parent: string) => insert(parent, id));
    vi.stubGlobal('createButton', undefined);
    vi.stubGlobal('insertChild', (parent: string, id: string, index: number) => insert(parent, id, index));
    vi.stubGlobal('moveWidget', (id: string, parent: string, index: number) => insert(parent, id, index));
    vi.stubGlobal('removeWidget', remove);
    vi.stubGlobal('setText', (id: string, text: string) => captions.set(id, text));
    vi.stubGlobal('on', (id: string, event: string, fn: (...args: unknown[]) => unknown) => listeners.set(`${id}:${event}`, fn));
});
afterEach(() => vi.unstubAllGlobals());

it('loads the standalone public entrypoint and its generated safe mock bridge', async () => {
    const { createMockBridge } = await import('../src/index.js');
    const bridge = createMockBridge();
    bridge.install();
    try {
        (globalThis as any).setText('caption', 'ready');
        expect(bridge.calls).toContainEqual({ fn: 'setText', args: ['caption', 'ready'] });
    } finally { bridge.uninstall(); }
});

describe('root child ordering', () => {
    it('inserts, reorders in both directions, appends an existing child and removes without stale indices', () => {
        const root = { rootId: 'root', nextId: 0 };
        const [a, b, c, d] = ['a', 'b', 'c', 'd'].map(id => instance('Label', { id, text: id }, root));
        for (const child of [a, b, c]) host.appendChildToContainer(root, child);
        host.insertInContainerBefore(root, c, a);
        expect(tree.get('root')).toEqual(['c', 'a', 'b']);
        host.insertInContainerBefore(root, c, b);
        expect(tree.get('root')).toEqual(['a', 'c', 'b']);
        host.insertInContainerBefore(root, d, c);
        expect(tree.get('root')).toEqual(['a', 'd', 'c', 'b']);
        host.appendChildToContainer(root, a);
        expect(tree.get('root')).toEqual(['d', 'c', 'b', 'a']);
        host.removeChildFromContainer(root, c);
        host.insertInContainerBefore(root, b, a);
        expect(tree.get('root')).toEqual(['d', 'b', 'a']);
    });
    it('clears only the requested container and can mount again', () => {
        const root = { rootId: 'root', nextId: 0 };
        const other = { rootId: 'other', nextId: 0 };
        host.appendChildToContainer(root, instance('Label', { id: 'a' }, root));
        host.appendChildToContainer(other, instance('Label', { id: 'b' }, other));
        host.clearContainer(root);
        expect(tree.get('root')).toEqual([]);
        expect(tree.get('other')).toEqual(['b']);
        host.appendChildToContainer(root, instance('Label', { id: 'c' }, root));
        expect(tree.get('root')).toEqual(['c']);
    });
});

it.each(['text', 'children'])('clears removed %s from native and public captions', key => {
    const root = { rootId: '', nextId: 0 };
    const node = instance('Label', { id: 'caption', [key]: 'old' }, root);
    node._dom = { _textContent: 'old' };
    host.appendChildToContainer(root, node);
    update(node, { id: 'caption' });
    expect(captions.get('caption')).toBe('');
    expect((node._dom as any)._textContent).toBe('');
});

it('replaces every transform component and resets an empty array to identity', () => {
    const state = { translate: [0, 0], rotation: 0, scale: 1, skew: [0, 0] };
    vi.stubGlobal('setTranslate', (_: string, x: number, y: number) => state.translate = [x, y]);
    vi.stubGlobal('setRotation', (_: string, degrees: number) => state.rotation = degrees);
    vi.stubGlobal('setScale', (_: string, value: number) => state.scale = value);
    vi.stubGlobal('setSkew', (_: string, x: number, y: number) => state.skew = [x, y]);
    applyTransformProp('a', 'transform', [{ translateX: 10 }, { scale: 2 }, { skewY: '20deg' }]);
    applyTransformProp('a', 'transform', [{ rotate: '45deg' }]);
    expect(state).toEqual({ translate: [0, 0], rotation: 45, scale: 1, skew: [0, 0] });
    applyTransformProp('a', 'transform', []);
    expect(state).toEqual({ translate: [0, 0], rotation: 0, scale: 1, skew: [0, 0] });
});

it.each([
    ['Knob', [0.5]], ['Fader', [0.3]], ['TextEditor', ['hello']],
    ['Checkbox', [1]], ['Toggle', [0]], ['XYPad', [0.2, 0.8]],
    ['Combo', [2]], ['Stepper', [3]], ['Pan', [-0.4]], ['ListBox', [1]],
])('delivers %s onChange payloads through initial and replacement handlers', (type, args) => {
    const root = { rootId: '', nextId: 0 };
    const first = vi.fn(); const second = vi.fn();
    const facade = (widgets as any)[type as string];
    const initialProps = facade({ id: 'control', onChange: first }).props;
    const node = instance(type as string, initialProps, root);
    const nativeEvent = type === 'Toggle' ? 'toggle' : ['Combo', 'ListBox'].includes(type as string) ? 'select' : 'change';
    const expected = ['Checkbox', 'Toggle'].includes(type as string) ? [Boolean((args as unknown[])[0])] : args;
    applyAllProps(node);
    listeners.get(`control:${nativeEvent}`)!(...(args as unknown[]));
    expect(first).toHaveBeenCalledWith(...(expected as unknown[]));
    applyChangedProps(node, node.props, facade({ id: 'control', onChange: second }).props);
    listeners.get(`control:${nativeEvent}`)!(...(args as unknown[]));
    expect(second).toHaveBeenCalledWith(...(expected as unknown[]));
});

it('preserves synthetic change events for HTML controls', () => {
    const root = { rootId: '', nextId: 0 };
    const handler = vi.fn();
    const node = instance('input', { id: 'input', onChange: handler }, root);
    applyAllProps(node);
    listeners.get('input:change')!('text');
    expect(handler.mock.calls[0][0]).toMatchObject({ type: 'change' });
});

it('preserves VirtualList selection and activation adapters through the public facade', () => {
    vi.stubGlobal('createVirtualList', (id: string, parent: string) => insert(parent, id));
    const onChange = vi.fn(); const onActivate = vi.fn();
    const root = createRoot();
    render(createElement(widgets.VirtualList, {
        id: 'list', rowCount: 0, rowHeight: 20, renderRow: () => null, onChange, onActivate,
    }), root);
    listeners.get('list:change')!([1, 3]);
    listeners.get('list:activate')!(3);
    expect(onChange).toHaveBeenCalledWith([1, 3]);
    expect(onActivate).toHaveBeenCalledWith(3);
    unmount(root);
});

it('updates and clears the capital Button fallback caption', () => {
    const root = { rootId: '', nextId: 0 };
    const node = instance('Button', { id: 'button', children: 'old' }, root);
    host.appendChildToContainer(root, node);
    update(node, { id: 'button', children: 'new' });
    expect(captions.get('button_l')).toBe('new');
    update(node, { id: 'button' });
    expect(captions.get('button_l')).toBe('');
});

it('uses moveWidget when the indexed insert function is unavailable', () => {
    vi.stubGlobal('insertChild', undefined);
    const root = { rootId: 'root', nextId: 0 };
    const a = instance('Label', { id: 'a' }, root);
    const b = instance('Label', { id: 'b' }, root);
    const c = instance('Label', { id: 'c' }, root);
    host.appendChildToContainer(root, a);
    host.appendChildToContainer(root, b);
    host.insertInContainerBefore(root, b, a);
    host.insertInContainerBefore(root, c, a);
    expect(tree.get('root')).toEqual(['b', 'c', 'a']);
});

it('inserts before a later nested sibling using its index after removal', () => {
    const root = { rootId: '', nextId: 0 };
    const parent = instance('Panel', { id: 'parent' }, root);
    host.appendChildToContainer(root, parent);
    const [a, b, c] = ['a', 'b', 'c'].map(id => instance('Label', { id }, root));
    for (const child of [a, b, c]) host.appendChild(parent, child);
    host.insertBefore(parent, a, c);
    expect(tree.get('parent')).toEqual(['b', 'a', 'c']);
    expect(parent.childIds).toEqual(['b', 'a', 'c']);
});

it('keeps native Button text on the widget when createButton exists', () => {
    vi.stubGlobal('createButton', (id: string, text: string, parent: string) => {
        insert(parent, id); captions.set(id, text);
    });
    const root = { rootId: '', nextId: 0 };
    const button = instance('Button', { id: 'button', text: 'old' }, root);
    host.appendChildToContainer(root, button);
    update(button, { id: 'button', text: 'new' });
    expect(captions.get('button')).toBe('new');
    expect(captions.has('button_l')).toBe(false);
});
