import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PulpHostConfig } from '../src/host-config.js';
import type { PulpContainer, PulpInstance } from '../src/types.js';

const host = PulpHostConfig as any;
let calls: unknown[][];
const root: PulpContainer = { rootId: 'root', nextId: 0 } as any;

function stub(name: string) {
    vi.stubGlobal(name, (...a: unknown[]) => { calls.push([name, ...a]); });
}
beforeEach(() => {
    calls = [];
    for (const n of ['createSvgPath', 'setSvgPath', 'createBadge', 'createLabel', 'createTextEditor',
        'createSvgRect', 'setSvgRect', 'createSvgLine', 'setSvgLine', 'createModal', 'setVisible', 'setText',
        'setPosition', 'setTop', 'setRight', 'setBottom', 'setLeft', 'setWidth', 'setHeight', 'setFlex',
        'setFlexGrow', 'setFlexShrink', 'setFlexBasis', 'setMargin', 'setPadding', 'setAlignSelf', 'setBackground']) stub(n);
});
afterEach(() => vi.unstubAllGlobals());

function make(type: string, props: Record<string, unknown>): PulpInstance {
    const n = host.createInstance(type, props, root, {}, {});
    host.appendChildToContainer(root, n);
    return n;
}
function upd(n: PulpInstance, props: Record<string, unknown>) {
    host.commitUpdate(n, true, n.type, n.props, props, {});
}
const names = () => calls.map((c) => c[0]);

describe('round 2 repairs', () => {
    it('639-r2-f1 circle geometry updates and clears', () => {
        const n = make('circle', { cx: 5, cy: 5, r: 3 });
        calls = [];
        upd(n, { cx: 5, cy: 5, r: 8 });
        expect(calls.find((c) => c[0] === 'setSvgPath')?.[2]).toContain('a 8 8');
        calls = [];
        upd(n, { cx: 5, cy: 5 });
        expect(calls.find((c) => c[0] === 'setSvgPath')?.[2]).toBe('');
    });
    it('639-r2-f2 Badge and badge are text-bearing and update their caption', () => {
        for (const t of ['Badge', 'badge']) {
            expect(host.shouldSetTextContent(t, { children: 'new' })).toBe(true);
            const n = make(t, { children: 'a' });
            calls = [];
            upd(n, { children: 'b' });
            expect(calls).toContainEqual(['setText', n.id, 'b']);
        }
    });
    it('639-r2-f3 textarea children initialize and update the editor text', () => {
        expect(host.shouldSetTextContent('textarea', { children: 'x' })).toBe(true);
        const n = make('textarea', { children: 'hello' });
        expect(calls).toContainEqual(['setText', n.id, 'hello']);
        calls = [];
        upd(n, { children: 'bye' });
        expect(calls).toContainEqual(['setText', n.id, 'bye']);
    });
    it('639-r2-f4 raw SVG rect/line string attributes become numbers', () => {
        const r = make('rect', { x: '1', y: '2', width: '30', height: '40' });
        expect(calls.find((c) => c[0] === 'setSvgRect')).toEqual(['setSvgRect', r.id, 1, 2, 30, 40]);
        const l = make('line', { x1: '1', y1: '2', x2: '3', y2: '4' });
        expect(calls.find((c) => c[0] === 'setSvgLine')).toEqual(['setSvgLine', l.id, 1, 2, 3, 4]);
        calls = [];
        upd(r, { x: '1', y: '2', width: '31', height: '40' });
        expect(calls.find((c) => c[0] === 'setSvgRect')).toEqual(['setSvgRect', r.id, 1, 2, 31, 40]);
    });
    it('639-r2-f5 Modal open drives native visibility on mount and update', () => {
        const m = make('Modal', { open: false });
        expect(calls).toContainEqual(['setVisible', m.id, false]);
        calls = [];
        upd(m, { open: true });
        expect(calls).toContainEqual(['setVisible', m.id, true]);
        expect(names()).not.toContain('createModal');
    });
});
