import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
    DESIGN_IR_PLOTS_VERSION,
    SUPPORTED_DESIGN_IR_VERSIONS,
    normalizeImport,
    validateDesignIR,
} from '../src/index.js';

const fixtures = new URL('../../../fixtures/design-ir/', import.meta.url);
const schema = new URL('../schema/design-ir-v1.schema.json', import.meta.url);

async function document() {
    const source = JSON.parse(await readFile(new URL('revision-a.source.json', fixtures), 'utf8'));
    return normalizeImport(source);
}

function withPlot(design, plot, schemaVersion = DESIGN_IR_PLOTS_VERSION) {
    const copy = structuredClone(design);
    copy.root.plot = plot;
    copy.schemaVersion = schemaVersion;
    return copy;
}

test('a node binds a plot to a value-feed channel without samples', async () => {
    const design = await document();
    const scope = {
        style: 'line', channel: 'output', source: 'scalar', slots: 240, history: 'sweep',
        min: -1, max: 1, lineWidth: 2, lineColor: '#F97316',
    };
    assert.deepEqual(validateDesignIR(withPlot(design, scope)), { issues: [], valid: true });
    const meter = { style: 'meter', channel: 'meters', audioChannel: 1, peakColor: '#FFFFFFCC' };
    assert.deepEqual(validateDesignIR(withPlot(design, meter)), { issues: [], valid: true });
});

test('a plot binding refuses what the renderer cannot draw', async () => {
    const design = await document();
    const cases = [
        [{ style: 'pie', channel: 'c', slots: 4 }, 'enum'],
        [{ style: 'line', slots: 4 }, 'type'],
        [{ style: 'line', channel: 'c' }, 'type'],
        [{ style: 'line', channel: 'c', slots: 0 }, 'range'],
        [{ style: 'line', channel: 'c', slots: 2.5 }, 'range'],
        [{ style: 'bar', channel: 'c', slots: 4, source: 'scalar' }, 'enum'],
        [{ style: 'meter', channel: 'c', source: 'vector' }, 'enum'],
        [{ style: 'line', channel: 'c', slots: 4, min: 1, max: 1 }, 'range'],
        [{ style: 'line', channel: 'c', slots: 4, lineWidth: -1 }, 'range'],
        [{ style: 'line', channel: 'c', slots: 4, barGap: 1e30 }, 'range'],
        [{ style: 'line', channel: 'c', slots: 4, lineColor: 'red' }, 'format'],
        [{ style: 'line', channel: 'c', slots: 4, samples: [1, 2] }, 'unknown-field'],
    ];
    for (const [plot, code] of cases) {
        const result = validateDesignIR(withPlot(design, plot));
        assert.equal(result.valid, false, JSON.stringify(plot));
        assert.ok(result.issues.some((entry) => entry.code === code), JSON.stringify(result.issues));
    }
});

test('plot bindings need schema version 5 and documents without them stay version 1', async () => {
    const design = await document();
    assert.equal(design.schemaVersion, 1);
    assert.deepEqual(validateDesignIR(design), { issues: [], valid: true });
    assert.deepEqual(SUPPORTED_DESIGN_IR_VERSIONS, [1, 2, 3, 4, 5]);
    const plot = { style: 'meter', channel: 'meters' };
    assert.deepEqual(validateDesignIR(withPlot(design, plot)), { issues: [], valid: true });
    const old = validateDesignIR(withPlot(design, plot, 1));
    assert.equal(old.valid, false);
    assert.ok(old.issues.some((entry) => entry.path === '$.root.plot' && entry.code === 'version'),
        JSON.stringify(old.issues));
    for (const version of [1, 2, 3, 4, 5]) {
        const plain = structuredClone(design);
        plain.schemaVersion = version;
        assert.deepEqual(validateDesignIR(plain), { issues: [], valid: true }, `version ${version}`);
    }
    for (const version of [0, 6, 1.5]) {
        const plain = structuredClone(design);
        plain.schemaVersion = version;
        const result = validateDesignIR(plain);
        assert.ok(result.issues.some((entry) => entry.path === '$.schemaVersion' &&
            entry.code === 'version'), `version ${version} is refused`);
    }
    for (const version of [2, 3, 4]) {
        const result = validateDesignIR(withPlot(design, plot, version));
        assert.ok(result.issues.some((entry) => entry.path === '$.root.plot' &&
            entry.code === 'version'), `a plot at version ${version} is refused`);
    }
    const nested = structuredClone(design);
    nested.schemaVersion = 1;
    nested.root.children.push({
        id: `${design.source.key}/nested-plot`, identity: { strategy: 'semantic', semanticId: 'nested-plot' },
        kind: 'plot', properties: {}, children: [], plot,
    });
    assert.ok(validateDesignIR(nested).issues.some((entry) => entry.code === 'version'),
        'a nested plot at version 1 is refused');
});

test('normalization keeps a source node plot binding', async () => {
    const source = JSON.parse(await readFile(new URL('revision-a.source.json', fixtures), 'utf8'));
    source.root.plot = { slots: 8, channel: 'spectrum', style: 'bar' };
    const design = normalizeImport(source);
    assert.deepEqual(design.root.plot, { channel: 'spectrum', slots: 8, style: 'bar' });
    assert.equal(design.schemaVersion, DESIGN_IR_PLOTS_VERSION);
    assert.deepEqual(validateDesignIR(design), { issues: [], valid: true });
});

test('the schema declares plot bindings without samples', async () => {
    const definitions = JSON.parse(await readFile(schema, 'utf8')).$defs;
    const published = JSON.parse(await readFile(schema, 'utf8'));
    assert.deepEqual(published.properties.schemaVersion.enum, [1, 2, 3, 4, 5]);
    assert.deepEqual(published.allOf, [{
        if: { properties: { schemaVersion: { enum: [1, 2, 3, 4] } } },
        then: { properties: { root: { $ref: '#/$defs/nodeWithoutPlot' } } },
    }]);
    assert.equal(definitions.nodeWithoutPlot.properties.plot, false);
    assert.deepEqual(definitions.nodeWithoutPlot.properties.children.items,
        { $ref: '#/$defs/nodeWithoutPlot' });
    assert.deepEqual(definitions.node.properties.plot, { $ref: '#/$defs/plotBinding' });
    assert.equal(definitions.plotBinding.additionalProperties, false);
    assert.deepEqual(definitions.plotBinding.required, ['style', 'channel']);
    assert.equal(definitions.plotBinding.properties.samples, undefined);
    assert.equal(definitions.plotBinding.properties.slots.maximum, 4194304);
    assert.deepEqual(definitions.plotBinding.if, { properties: { style: { const: 'meter' } } });
    assert.deepEqual(definitions.plotBinding.else, { required: ['slots'] });
    assert.equal(definitions.plotBinding.properties.lineWidth.maximum, 4096);
});
