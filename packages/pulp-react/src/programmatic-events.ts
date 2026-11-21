// Native text and selection setters notify synchronously. Suppress only the
// change events caused by renderer-owned writes, including batched writes.
const writing = new Map<string, number>();
const notifyingSetters = new Set([
    'setText', 'setValue', 'setSelected', 'setListSelected', 'setItems',
    'setListItems', 'setXY', 'setMin', 'setMax', 'setStep',
]);

export function rendererWrite<T>(name: string, id: unknown, invoke: () => T): T {
    if (typeof id !== 'string' || !notifyingSetters.has(name)) return invoke();
    writing.set(id, (writing.get(id) ?? 0) + 1);
    try { return invoke(); }
    finally {
        const depth = (writing.get(id) ?? 1) - 1;
        if (depth) writing.set(id, depth);
        else writing.delete(id);
    }
}

export function isRendererChange(id: string, event: string): boolean {
    return writing.has(id) && ['change', 'input', 'select', 'toggle'].includes(event);
}
