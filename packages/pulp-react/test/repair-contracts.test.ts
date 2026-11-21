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
    vi.stubGlobal('createCol', (id: string, parent: string) => insert(parent, id));
    vi.stubGlobal('createRow', (id: string, parent: string) => insert(parent, id));
    vi.stubGlobal('createButton', undefined);
    vi.stubGlobal('insertChild', (parent: string, id: string, index: number) => insert(parent, id, index));
    vi.stubGlobal('moveWidget', (id: string, parent: string, index: number) => insert(parent, id, index));
    vi.stubGlobal('removeWidget', remove);
    vi.stubGlobal('setText', (id: string, text: string) => captions.set(id, text));
    for (const name of ['setPosition', 'setTop', 'setRight', 'setBottom', 'setLeft']) {
        vi.stubGlobal(name, () => {});
    }
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

it('keeps a caption target when an HTML container changes from nested markup to text', () => {
    const root = { rootId: '', nextId: 0 };
    const node = instance('div', { id: 'container', children: { type: 'span' } }, root);
    host.appendChildToContainer(root, node);
    expect(captions.has('container__text')).toBe(true);
    update(node, { id: 'container', children: 'ready' });
    expect(captions.get('container__text')).toBe('ready');
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

it('moves an existing nested child to the end without duplicate bookkeeping', () => {
    const root = { rootId: '', nextId: 0 };
    const parent = instance('Panel', { id: 'parent' }, root);
    host.appendChildToContainer(root, parent);
    const [a, b, c] = ['a', 'b', 'c'].map(id => instance('Label', { id }, root));
    for (const child of [a, b, c]) host.appendChild(parent, child);
    host.appendChild(parent, a);
    expect(tree.get('parent')).toEqual(['b', 'c', 'a']);
    expect(parent.childIds).toEqual(['b', 'c', 'a']);
    host.insertBefore(parent, a, c);
    expect(tree.get('parent')).toEqual(['b', 'a', 'c']);
});

it('reorders deferred children without duplicating their pending entries', () => {
    const root = { rootId: '', nextId: 0 };
    const parent = instance('Panel', { id: 'parent' }, root);
    const [a, b, c] = ['a', 'b', 'c'].map(id => instance('Label', { id }, root));
    for (const child of [a, b, c]) host.appendInitialChild(parent, child);
    host.appendChild(parent, a);
    host.insertBefore(parent, b, a);
    host.appendChildToContainer(root, parent);
    expect(tree.get('parent')).toEqual(['c', 'b', 'a']);
    expect(parent.childIds).toEqual(['c', 'b', 'a']);
});

it('commits synthetic text to native captions and anonymous-text metadata', () => {
    const root = { rootId: '', nextId: 0 };
    const parent = instance('Panel', { id: 'parent' }, root);
    parent._dom = { _children: [] };
    const text = host.createTextInstance('one', root, {}, {});
    host.appendInitialChild(parent, text);
    host.appendChildToContainer(root, parent);
    (globalThis as any).__pulpReactDomRegistry__.set('parent', parent._dom);
    host.commitTextUpdate(text, 'one', 'two');
    expect(captions.get(text.id)).toBe('two');
    expect(text.props).toMatchObject({ text: 'two', children: 'two' });
    expect((parent._dom as any).__pulpAnonymousTextTargets).toEqual([{ id: text.id, text: 'two' }]);
    host.commitTextUpdate(text, 'two', '');
    expect(captions.get(text.id)).toBe('');
    expect((parent._dom as any).__pulpAnonymousTextTargets[0].text).toBe('');
});

it('gives anonymous widgets distinct bridge IDs across default roots', () => {
    const first = createRoot('first');
    const second = createRoot('second');
    const a = instance('Label', {}, first);
    const b = instance('Label', {}, second);
    const text = host.createTextInstance('caption', createRoot('third'), {}, {});
    expect(new Set([a.id, b.id, text.id]).size).toBe(3);
    host.appendChildToContainer(first, a);
    host.appendChildToContainer(second, b);
    update(a, { text: 'first' });
    update(b, { text: 'second' });
    expect(captions.get(a.id)).toBe('first');
    expect(captions.get(b.id)).toBe('second');
    const explicit = instance('Label', {}, createRoot('custom', 'app_'));
    expect(explicit.id).toBe('app_1');
});

it('keeps nested DOM children in native order after insertBefore', () => {
    const root = { rootId: '', nextId: 0 };
    const parent = instance('Panel', { id: 'parent' }, root);
    parent._dom = { _children: [] };
    host.appendChildToContainer(root, parent);
    const [a, b, c] = ['a', 'b', 'c'].map(id => instance('Label', { id }, root));
    for (const child of [a, b, c]) {
        child._dom = { id: child.id };
        host.appendChild(parent, child);
    }
    host.insertBefore(parent, a, c);
    expect(tree.get('parent')).toEqual(['b', 'a', 'c']);
    expect((parent._dom as any)._children.map((child: any) => child.id)).toEqual(['b', 'a', 'c']);
});

it.each(['Combo', 'ListBox'])('applies %s options before selection and resets removed props', type => {
    const state = { options: [] as string[], selected: -1 };
    vi.stubGlobal(type === 'Combo' ? 'setItems' : 'setListItems', (_: string, values: string[]) => {
        state.options = values; state.selected = -1;
    });
    vi.stubGlobal(type === 'Combo' ? 'setSelected' : 'setListSelected', (_: string, value: number) => state.selected = value);
    const root = createRoot();
    const initial = { id: 'select', selected: 1, options: ['A', 'B'] };
    const node = instance(type, initial, root);
    applyAllProps(node);
    expect(state).toEqual({ options: ['A', 'B'], selected: 1 });
    const changed = { ...initial, options: ['C', 'D'] };
    applyChangedProps(node, initial, changed);
    expect(state).toEqual({ options: ['C', 'D'], selected: 1 });
    applyChangedProps(node, changed, { id: 'select' });
    expect(state).toEqual({ options: [], selected: type === 'Combo' ? 0 : -1 });
});

it('updates XYPad coordinates together and restores removed axes to native defaults', () => {
    const setXY = vi.fn();
    vi.stubGlobal('setXY', setXY);
    const root = createRoot();
    const initial = { id: 'xy', x: 0.2, y: 0.8 };
    const node = instance('XYPad', initial, root);
    applyAllProps(node);
    expect(setXY).toHaveBeenCalledExactlyOnceWith('xy', 0.2, 0.8);
    setXY.mockClear();
    applyChangedProps(node, initial, { id: 'xy', y: 0.7 });
    expect(setXY).toHaveBeenCalledExactlyOnceWith('xy', 0.5, 0.7);
    setXY.mockClear();
    applyChangedProps(node, { id: 'xy', y: 0.7 }, { id: 'xy' });
    expect(setXY).toHaveBeenCalledExactlyOnceWith('xy', 0.5, 0.5);
});

it('recalculates font-relative typography when fontSize changes or is removed', () => {
    const setLineHeight = vi.fn();
    const setLetterSpacing = vi.fn();
    vi.stubGlobal('setLineHeight', setLineHeight);
    vi.stubGlobal('setLetterSpacing', setLetterSpacing);
    const root = createRoot();
    const initial = { id: 'type', fontSize: 10, lineHeight: 1.5, letterSpacing: '0.1em' };
    const node = instance('Label', initial, root);
    applyAllProps(node);
    setLineHeight.mockClear(); setLetterSpacing.mockClear();
    applyChangedProps(node, initial, { ...initial, fontSize: 20 });
    expect(setLineHeight).toHaveBeenCalledWith('type', 30);
    expect(setLetterSpacing).toHaveBeenCalledWith('type', 2);
    setLineHeight.mockClear(); setLetterSpacing.mockClear();
    applyChangedProps(node, { ...initial, fontSize: 20 }, {
        id: 'type', lineHeight: 1.5, letterSpacing: '0.1em',
    });
    expect(setLineHeight).toHaveBeenCalledWith('type', 21);
    expect(setLetterSpacing).toHaveBeenCalledWith('type', 1.4000000000000001);
});

it('coalesces ScrollView offsets so changing one axis preserves the other', () => {
    const scrollTo = vi.fn();
    vi.stubGlobal('scrollTo', scrollTo);
    const root = createRoot();
    const initial = { id: 'scroll', scrollTop: 10, scrollLeft: 20 };
    const node = instance('ScrollView', initial, root);
    applyAllProps(node);
    expect(scrollTo).toHaveBeenCalledExactlyOnceWith('scroll', 20, 10);
    scrollTo.mockClear();
    applyChangedProps(node, initial, { ...initial, scrollTop: 30 });
    expect(scrollTo).toHaveBeenCalledExactlyOnceWith('scroll', 20, 30);
});
