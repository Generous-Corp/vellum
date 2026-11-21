import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { _debugResetRegistry, parseShortcut, registerShortcut } from '../src/shortcuts.js';

beforeEach(() => _debugResetRegistry());
afterEach(() => { _debugResetRegistry(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

// Values from vellum::view::KeyCode in input_events.hpp, not DOM key codes.
it.each([
    ['arrowleft', 256], ['arrowright', 257], ['arrowup', 258], ['arrowdown', 259],
    ['backspace', 270], ['delete', 271], ['tab', 272], ['enter', 273],
    ['return', 273], ['escape', 274], ['esc', 274], ['f1', 290], ['f12', 301],
    ['space', 32], ['a', 97],
])('maps %s to the native key code', (spec, code) => {
    expect(parseShortcut(spec as string).keyCode).toBe(code);
});

it.each([['esc', 'escape'], ['return', 'enter'], ['ctrl+ctrl+s', 'control+s']])(
    'shares one dispatcher across %s and %s', (firstSpec, secondSpec) => {
        const nativeRegister = vi.fn();
        vi.stubGlobal('registerShortcut', nativeRegister);
        vi.spyOn(console, 'warn').mockImplementation(() => {});
        const first = vi.fn(); const second = vi.fn();
        const unregisterFirst = registerShortcut(firstSpec, first, 'first');
        const unregisterSecond = registerShortcut(secondSpec, second, 'second');
        expect(nativeRegister).toHaveBeenCalledTimes(1);
        const dispatch = (globalThis as any)[nativeRegister.mock.calls[0][2]];
        dispatch();
        expect(second).toHaveBeenCalledTimes(1);
        expect(first).not.toHaveBeenCalled();
        unregisterSecond();
        dispatch();
        expect(first).toHaveBeenCalledTimes(1);
        unregisterFirst();
        dispatch();
        expect(first).toHaveBeenCalledTimes(1);
    },
);

it('installs a dispatcher when the native bridge becomes available', () => {
    vi.stubGlobal('registerShortcut', undefined);
    registerShortcut('ctrl+s', vi.fn());
    const nativeRegister = vi.fn();
    vi.stubGlobal('registerShortcut', nativeRegister);
    registerShortcut('ctrl+s', vi.fn());
    expect(nativeRegister).toHaveBeenCalledTimes(1);
});
