// Writes the retained trees the DesignIR-to-GPU test draws: a DesignIR
// document normalized by @vellum/design-ir and materialized by @vellum/ui, once
// with its compositing and once with it stripped, as the negative control.
import { writeFile } from 'node:fs/promises';

import { normalizeImport } from '../../packages/vellum-design-ir/src/index.js';
import { createApp, materializeDesign, mount } from '../../packages/vellum-ui/src/index.js';

const [composited, plain] = process.argv.slice(2);
if (!composited || !plain) throw new Error('usage: design_gpu_tree.mjs <composited.json> <plain.json>');

function box(sourceId, layout, color, compositing, children = []) {
    return {
        kind: 'view',
        sourceId,
        properties: { layout, paint: { backgroundColor: color } },
        ...(compositing ? { compositing } : {}),
        children,
    };
}

function design(withCompositing) {
    const use = (compositing) => (withCompositing ? compositing : undefined);
    return normalizeImport({
        source: {
            key: 'screen',
            adapter: 'fixture',
            adapterVersion: '1',
            formatVersion: '1',
            revision: 'design-gpu',
        },
        root: box('root', { width: 96, height: 64 }, '#336699', undefined, [
            box('backdrop', { x: 8, y: 8, width: 40, height: 48 }, '#CC5926'),
            box('blended', { x: 28, y: 16, width: 56, height: 32 }, '#40A6E6',
                use({ blendMode: 'multiply', opacity: 0.5 })),
            box('cut', { x: 56, y: 52, width: 40, height: 12 }, '#336699', use({ isolate: true }), [
                box('content', { x: 0, y: 0, width: 36, height: 12 }, '#40A6E6',
                    use({ maskRef: 'screen/mask' })),
                box('mask', { x: 0, y: 0, width: 18, height: 12 }, '#000000', use({ mask: 'alpha' })),
            ]),
        ]),
    });
}

async function render(document, path) {
    const app = createApp({
        id: 'design-gpu',
        stateVersion: '1',
        initialState: {},
        actions: {},
        render: () => materializeDesign(document),
    });
    await writeFile(path, mount(app).renderJSON());
}

const document = design(true);
if (document.schemaVersion !== 2) throw new Error('the composited design is not schema version 2');
await render(document, composited);
const control = design(false);
if (control.schemaVersion !== 1) throw new Error('the plain design is not schema version 1');
await render(control, plain);
