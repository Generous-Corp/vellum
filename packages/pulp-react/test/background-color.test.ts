import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PulpHostConfig } from '../src/host-config.js';
import type { PulpContainer, PulpInstance } from '../src/types.js';
import { bridgeMockFunctionNames } from '../src/bridge-mock-functions.generated.js';

const host = PulpHostConfig as any;
let calls: unknown[][];
const root: PulpContainer = { rootId: 'root', nextId: 0 } as any;

beforeEach(() => {
    calls = [];
    for (const n of bridgeMockFunctionNames) {
        vi.stubGlobal(n, (...a: unknown[]) => { calls.push([n, ...a]); });
    }
});
afterEach(() => vi.unstubAllGlobals());

function make(type: string, props: Record<string, unknown>): PulpInstance {
    const n = host.createInstance(type, props, root, {}, {});
    host.appendChildToContainer(root, n);
    return n;
}

describe('style.backgroundColor', () => {
    it('paints the background of an HTML-style element', () => {
        const n = make('div', { style: { backgroundColor: '#ff0000' } });
        expect(calls).toContainEqual(['setBackground', n.id, '#ff0000']);
    });
    it('repaints when the colour changes', () => {
        const n = make('div', { style: { backgroundColor: '#ff0000' } });
        calls = [];
        host.commitUpdate(n, true, n.type, n.props, { style: { backgroundColor: '#00ff00' } }, {});
        expect(calls).toContainEqual(['setBackground', n.id, '#00ff00']);
    });
    it('clears the background when the colour is removed', () => {
        for (const next of [{}, { backgroundColor: null }]) {
            const n = make('div', { style: { backgroundColor: '#ff0000' } });
            calls = [];
            host.commitUpdate(n, true, n.type, n.props, { style: next }, {});
            expect(calls).toContainEqual(['setBackground', n.id, '']);
        }
    });
});
