import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
    decodeFigmaPluginExport,
    normalizeImport,
} from '../src/index.js';

const repository = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const fixtures = join(repository, 'fixtures', 'design-ir');

function genericExport() {
    return {
        $schema: 'https://pulp.dev/schemas/figma-plugin-export-v1.json',
        format_version: '2026.05-figma-plugin-v1',
        parser_version: '0.1.0',
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
                content_hash: 'd'.repeat(64),
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
    assert.equal(document.assets[0].contentHash, `sha256:${'d'.repeat(64)}`);
    assert.equal(document.assets[0].uri, 'assets/mark.svg');
    assert.ok(document.diagnostics.some((diagnostic) =>
        diagnostic.code === 'figma-property-preserved-not-materialized' &&
        diagnostic.property === 'box_shadow' &&
        diagnostic.disposition === 'unsupported',
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

test('rejects unknown formats, parser revisions, and non-generic node types', () => {
    const unknownFormat = genericExport();
    unknownFormat.format_version = 'future-format';
    assert.throws(
        () => decodeFigmaPluginExport(unknownFormat, {
            sourceHash: `sha256:${'c'.repeat(64)}`,
            sourceKey: 'main',
        }),
        /format_version/,
    );

    const unknownParser = genericExport();
    unknownParser.parser_version = '0.2.0';
    assert.throws(
        () => decodeFigmaPluginExport(unknownParser, {
            sourceHash: `sha256:${'c'.repeat(64)}`,
            sourceKey: 'main',
        }),
        /parser_version/,
    );

    const unknownNode = genericExport();
    unknownNode.root.type = 'table';
    assert.throws(
        () => decodeFigmaPluginExport(unknownNode, {
            sourceHash: `sha256:${'d'.repeat(64)}`,
            sourceKey: 'main',
        }),
        /pinned Figma subset/,
    );
});

test('checked-in proof is byte-produced by the pinned Pulp serializer and asset cache', async () => {
    const fixturePath = join(fixtures, 'pulp-emitter-generic.export.json');
    const receipt = JSON.parse(await readFile(
        join(fixtures, 'pulp-emitter-generic.receipt.json'),
        'utf8',
    ));
    const fixtureBytes = await readFile(fixturePath);
    assert.equal(receipt.schema, 'vellum.pulp-figma-emitter-fixture-receipt.v1');
    assert.equal(receipt.emitter.repository, 'Generous-Corp/pulp');
    assert.match(receipt.emitter.commit, /^[0-9a-f]{40}$/);
    assert.deepEqual(
        Object.keys(receipt.emitter.sourceBlobs).sort(),
        [
            'tools/figma-plugin/src/assets.ts',
            'tools/figma-plugin/src/extract-model.ts',
            'tools/figma-plugin/src/serialize.ts',
            'tools/figma-plugin/src/ui.ts',
        ],
    );
    for (const blob of Object.values(receipt.emitter.sourceBlobs)) {
        assert.match(blob, /^[0-9a-f]{40}$/);
    }
    assert.equal(sha256(fixtureBytes), receipt.fixture.sha256);

    const inputBytes = await readFile(join(fixtures, receipt.generatorInput.path));
    assert.equal(sha256(inputBytes), receipt.generatorInput.sha256);

    const assetBytes = await readFile(join(fixtures, receipt.asset.path));
    assert.equal(sha256(assetBytes), receipt.asset.sha256);
    const archiveBytes = await readFile(join(fixtures, receipt.archive.path));
    assert.equal(sha256(archiveBytes), receipt.archive.sha256);
    assert.equal(receipt.archive.sceneMember, 'scene.pulp.json');
    assert.equal(receipt.archive.writer.name, 'fflate');
    assert.match(receipt.archive.writer.version, /^0\.8\./);
    const envelope = JSON.parse(fixtureBytes.toString('utf8'));
    assert.equal(envelope.parser_version, '0.1.0');
    assert.equal(envelope.asset_manifest.assets[0].content_hash, receipt.asset.sha256);

    const document = normalizeImport(decodeFigmaPluginExport(envelope, {
        sourceHash: `sha256:${sha256(fixtureBytes)}`,
        sourceKey: 'main',
    }));
    assert.deepEqual(
        [document.root.kind, ...document.root.children.map((node) => node.kind)],
        ['view', 'text', 'view'],
    );
    assert.equal(document.assets[0].contentHash, `sha256:${receipt.asset.sha256}`);
    assert.equal(
        document.root.children[1].extensions['dev.vellum.figma-plugin.v1'].assetRef,
        envelope.root.children[1].asset_ref,
    );
    assert.ok(document.diagnostics.some((diagnostic) =>
        diagnostic.code === 'figma-node-preserved-not-materialized' &&
        diagnostic.detail.sourceKind === 'image' &&
        diagnostic.disposition === 'unsupported',
    ));
    assert.ok(document.diagnostics.some((diagnostic) =>
        diagnostic.property === 'font_family' && diagnostic.disposition === 'unsupported',
    ));
});

test("neutral audio_widget:'none' mask wrappers lower without accepting audio behavior", () => {
    const input = genericExport();
    input.root.children.push({
        attributes: { mask_role: 'clip' },
        audio_widget: 'none',
        children: [],
        constraints: { horizontal: 'CENTER', vertical: 'CENTER' },
        figma_node_id: '1:9',
        name: 'Mask ellipse',
        style: { background_color: '#ffffff', height: 40, width: 40 },
        type: 'ellipse',
    });
    const document = normalizeImport(decodeFigmaPluginExport(input, {
        sourceHash: `sha256:${'e'.repeat(64)}`,
        sourceKey: 'main',
    }));
    const ellipse = document.root.children.at(-1);
    assert.equal(ellipse.kind, 'view');
    assert.equal(ellipse.properties.paint.borderRadius, 20);
    assert.equal(
        ellipse.extensions['dev.vellum.figma-plugin.v1'].audioWidgetSentinel,
        'none',
    );
    assert.deepEqual(
        ellipse.extensions['dev.vellum.figma-plugin.v1'].sourceFields.constraints,
        { horizontal: 'CENTER', vertical: 'CENTER' },
    );
    assert.ok(document.diagnostics.some((diagnostic) =>
        diagnostic.code === 'figma-audio-none-sentinel-ignored',
    ));
});

test('maps every known Pulp diagnostic loss kind and preserves property and anchor evidence', () => {
    const expectations = new Map([
        ['capture_partial', 'unsupported'],
        ['fallback_used', 'lowered'],
        ['rasterized', 'rasterized'],
        ['recognition_unavailable', 'unsupported'],
        ['unknown', 'unsupported'],
        ['unresolved_asset', 'unsupported'],
        ['unsupported_node', 'unsupported'],
        ['unsupported_property', 'unsupported'],
    ]);
    for (const [kind, disposition] of expectations) {
        const input = genericExport();
        input.diagnostics = [{
            anchor_id: 'figma:1:3',
            code: `source-${kind}`,
            kind,
            message: `Source reported ${kind}`,
            path: '/root/children/0',
            property: 'filter',
            provider_detail: { retained: true },
            severity: 'warning',
        }];
        const document = normalizeImport(decodeFigmaPluginExport(input, {
            sourceHash: `sha256:${'f'.repeat(64)}`,
            sourceKey: 'main',
        }));
        const diagnostic = document.diagnostics.find((item) => item.code === `source-${kind}`);
        assert.equal(diagnostic.disposition, disposition);
        assert.equal(diagnostic.property, 'filter');
        assert.equal(diagnostic.detail.sourceAnchorId, 'figma:1:3');
        assert.deepEqual(diagnostic.detail.sourceFields.provider_detail, { retained: true });
    }

    const input = genericExport();
    input.diagnostics[0].kind = 'future_loss_kind';
    assert.throws(
        () => decodeFigmaPluginExport(input, {
            sourceHash: `sha256:${'f'.repeat(64)}`,
            sourceKey: 'main',
        }),
        /diagnostic kind/,
    );
});

test('rejects synthetic multi-selection roots explicitly', () => {
    const input = genericExport();
    input.root.name = '<multi-export>';
    assert.throws(
        () => decodeFigmaPluginExport(input, {
            sourceHash: `sha256:${'1'.repeat(64)}`,
            sourceKey: 'main',
        }),
        /exactly one root frame/,
    );
});

function sha256(bytes) {
    return createHash('sha256').update(bytes).digest('hex');
}
