import assert from 'node:assert/strict';
import test from 'node:test';

import {
    decodeFigmaPluginExport,
    normalizeImport,
} from '../src/index.js';

function genericExport() {
    return {
        $schema: 'https://pulp.dev/schemas/figma-plugin-export-v1.json',
        format_version: '2026.05-figma-plugin-v1',
        parser_version: '0.2.0',
        compat_schema_version: '0.3',
        provenance: {
            adapter: 'figma-plugin',
            version: '0.2.0',
            source_uri: 'figma://abc123/1:2',
            exported_at: '2026-07-22T00:00:00.000Z',
        },
        tokens: {
            colors: { accent: '#22c55e' },
            dimensions: { spacing: 12 },
            strings: { title: 'Palette Board' },
        },
        asset_manifest: {
            version: 1,
            assets: [{
                asset_id: 'mark',
                content_hash: 'sha256:deadbeef',
                local_path: 'assets/mark.svg',
                mime: 'image/svg+xml',
                width: 24,
                height: 24,
            }],
        },
        diagnostics: [{
            code: 'font-substitution',
            kind: 'fallback_used',
            message: 'The source font requires a portable fallback',
            path: '/root/children/0',
            severity: 'warning',
        }],
        root: {
            type: 'frame',
            name: 'Palette Board',
            figma_node_id: '1:2',
            style: {
                width: 640,
                height: 400,
                background_color: '#0f172a',
                box_shadow: '0 4px 20px #0008',
            },
            layout: { display: 'flex', direction: 'column', gap: 12 },
            children: [{
                type: 'text',
                name: 'Title',
                figma_node_id: '1:3',
                content: 'Palette Board',
                style: { color: '#ffffff', font_size: 28 },
                layout: {},
                children: [],
            }, {
                type: 'button',
                name: 'Create board',
                figma_node_id: '1:4',
                style: { background_color: '#22c55e', border_radius: 8 },
                layout: {},
                children: [],
            }],
        },
    };
}

test('decodes a generic Figma plugin export into deterministic DesignIR input', () => {
    const source = decodeFigmaPluginExport(genericExport(), {
        sourceHash: `sha256:${'a'.repeat(64)}`,
        sourceKey: 'main',
    });
    const document = normalizeImport(source);

    assert.equal(document.source.adapter, 'figma-plugin');
    assert.equal(document.source.providerFileKey, 'abc123');
    assert.equal(document.source.providerNodeId, '1:2');
    assert.equal(document.source.revision, 'figma-aaaaaaaaaaaaaaaa');
    assert.equal(document.root.id, 'main/1:2');
    assert.equal(document.root.children[0].id, 'main/1:3');
    assert.equal(document.root.children[1].role, 'button');
    assert.equal(document.root.properties.paint.backgroundColor, '#0f172a');
    assert.equal(document.root.properties.layout.width, 640);
    assert.equal(document.root.properties.layout.direction, 'column');
    assert.equal(document.root.children[0].properties.text.fontSize, 28);
    assert.equal(document.tokens['main.color.accent'].$value, '#22c55e');
    assert.deepEqual(document.tokens['main.dimension.spacing'].$value, {
        unit: 'px', value: 12,
    });
    assert.equal(document.assets[0].id, 'mark');
    assert.equal(document.assets[0].uri, 'assets/mark.svg');
    assert.ok(document.diagnostics.some((diagnostic) =>
        diagnostic.code === 'figma-property-preserved-not-materialized' &&
        diagnostic.property === 'box_shadow' &&
        diagnostic.disposition === 'extension',
    ));
    assert.ok(document.diagnostics.some((diagnostic) =>
        diagnostic.code === 'font-substitution' && diagnostic.disposition === 'lowered',
    ));
});

test('fails closed on audio-widget kinds, fields, and binding attributes', () => {
    for (const mutation of [
        (input) => { input.root.type = 'knob'; },
        (input) => { input.root.audio_widget = 'knob'; },
        (input) => { input.root.attributes = { binding: 'filter.cutoff' }; },
    ]) {
        const input = genericExport();
        mutation(input);
        assert.throws(
            () => decodeFigmaPluginExport(input, {
                sourceHash: `sha256:${'b'.repeat(64)}`,
                sourceKey: 'main',
            }),
            /audio/i,
        );
    }
});

test('rejects unknown formats and non-generic node types', () => {
    const unknownFormat = genericExport();
    unknownFormat.format_version = 'future-format';
    assert.throws(
        () => decodeFigmaPluginExport(unknownFormat, {
            sourceHash: `sha256:${'c'.repeat(64)}`,
            sourceKey: 'main',
        }),
        /unsupported Figma plugin format/,
    );

    const unknownNode = genericExport();
    unknownNode.root.type = 'table';
    assert.throws(
        () => decodeFigmaPluginExport(unknownNode, {
            sourceHash: `sha256:${'d'.repeat(64)}`,
            sourceKey: 'main',
        }),
        /generic Figma subset/,
    );
});
