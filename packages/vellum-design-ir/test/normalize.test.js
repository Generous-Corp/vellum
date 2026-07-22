import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
    DESIGN_IR_SCHEMA,
    DesignIRValidationError,
    indexTree,
    normalizeImport,
    parseDesignIR,
    stableStringify,
    summarizeDesignIR,
    validateDesignIR,
} from '../src/index.js';

const fixtures = new URL('../../../fixtures/design-ir/', import.meta.url);

async function fixture(name) {
    return JSON.parse(await readFile(new URL(name, fixtures), 'utf8'));
}

test('normalization is deterministic, canonical, and versioned', async () => {
    const source = await fixture('revision-a.source.json');
    const first = normalizeImport(source);
    const second = normalizeImport(reverseObjectKeys(source));

    assert.equal(first.$schema, DESIGN_IR_SCHEMA);
    assert.equal(first.schemaVersion, 1);
    assert.equal(stableStringify(first), stableStringify(second));
    assert.deepEqual(validateDesignIR(first), { issues: [], valid: true });

    const rewritten = stableStringify(parseDesignIR(stableStringify(first)));
    assert.equal(rewritten, stableStringify(first));
    assert.deepEqual(summarizeDesignIR(first), {
        assets: 1,
        diagnostics: 1,
        identityStrategies: { provider: 9, structural: 1 },
        kinds: { button: 2, text: 2, view: 6 },
        losses: 0,
        nodes: 10,
        revision: 'palette-board-a',
        schemaVersion: 1,
        sourceKey: 'main',
        tokens: 5,
    });
});

test('provider IDs are namespaced and structural siblings never collide', () => {
    const document = normalizeImport({
        source: {
            key: 'screen',
            adapter: 'fixture',
            adapterVersion: '1',
            formatVersion: '1',
            revision: 'a',
        },
        root: {
            kind: 'view',
            sourceId: '0:1',
            children: [
                { kind: 'text', name: 'Duplicate', text: 'Same', children: [] },
                { kind: 'text', name: 'Duplicate', text: 'Same', children: [] },
            ],
        },
    });
    const ids = [...indexTree(document.root).index.keys()];
    assert.equal(ids[0], 'screen/0:1');
    assert.equal(new Set(ids).size, ids.length);
    assert.match(ids[1], /^screen\/generated-/);
    assert.notEqual(ids[1], ids[2]);
});

test('structurally identical nodes under different parents remain globally unique', () => {
    const repeated = { kind: 'text', name: 'Untitled', text: 'Same', children: [] };
    const document = normalizeImport({
        source: {
            key: 'screen',
            adapter: 'fixture',
            adapterVersion: '1',
            formatVersion: '1',
            revision: 'a',
        },
        root: {
            kind: 'view',
            sourceId: 'root',
            children: [
                { kind: 'view', sourceId: 'left', children: [repeated] },
                { kind: 'view', sourceId: 'right', children: [repeated] },
            ],
        },
    });
    const ids = [...indexTree(document.root).index.keys()];
    assert.equal(new Set(ids).size, ids.length);
});

test('duplicate provider identities fail closed', () => {
    assert.throws(
        () => normalizeImport({
            source: {
                key: 'main',
                adapter: 'fixture',
                adapterVersion: '1',
                formatVersion: '1',
                revision: 'a',
            },
            root: {
                kind: 'view',
                sourceId: 'root',
                children: [
                    { kind: 'view', sourceId: 'same', children: [] },
                    { kind: 'view', sourceId: 'same', children: [] },
                ],
            },
        }),
        /duplicate stable identity 'main\/same'/,
    );
});

test('unknown adapter fields are preserved and diagnosed instead of dropped', () => {
    const document = normalizeImport({
        source: {
            key: 'main',
            adapter: 'future-adapter',
            adapterVersion: '2',
            formatVersion: 'future-v1',
            revision: 'a',
        },
        root: {
            kind: 'view',
            sourceId: 'root',
            futureBlend: { mode: 'spectral' },
            losses: [{
                code: 'future-blend-unsupported',
                disposition: 'unsupported',
                message: 'The renderer does not implement this blend mode',
                property: 'futureBlend',
                remediation: 'Use normal blend mode or a custom component',
                severity: 'warning',
            }],
            children: [],
        },
    });

    assert.deepEqual(
        document.root.extensions['dev.vellum.import.unrecognized.v1'],
        { futureBlend: { mode: 'spectral' } },
    );
    assert.equal(document.lossReport.lossCount, 1);
    assert.deepEqual(
        document.diagnostics.map(({ code, disposition }) => ({ code, disposition })),
        [
            { code: 'future-blend-unsupported', disposition: 'unsupported' },
            { code: 'source-node-fields-preserved', disposition: 'extension' },
        ],
    );
});

test('unknown schema versions are rejected rather than best-effort parsed', async () => {
    const document = normalizeImport(await fixture('revision-a.source.json'));
    document.schemaVersion = 2;
    assert.throws(() => parseDesignIR(document), DesignIRValidationError);
});

function reverseObjectKeys(value) {
    if (Array.isArray(value)) return value.map(reverseObjectKeys);
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(
        Object.keys(value)
            .reverse()
            .map((key) => [key, reverseObjectKeys(value[key])]),
    );
}
