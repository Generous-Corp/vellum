import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { normalizeImport, validateDesignIR } from '../src/index.js';

const fixtures = new URL('../../../fixtures/design-ir/', import.meta.url);
const schema = new URL('../schema/design-ir-v1.schema.json', import.meta.url);

async function document() {
    const source = JSON.parse(await readFile(new URL('revision-a.source.json', fixtures), 'utf8'));
    return normalizeImport(source);
}

function withPlot(design, plot) {
    const copy = structuredClone(design);
    copy.root.plot = plot;
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
        [{ style: 'line', channel: 'c', slots: 4, lineColor: 'red' }, 'format'],
        [{ style: 'line', channel: 'c', slots: 4, samples: [1, 2] }, 'unknown-field'],
    ];
    for (const [plot, code] of cases) {
        const result = validateDesignIR(withPlot(design, plot));
        assert.equal(result.valid, false, JSON.stringify(plot));
        assert.ok(result.issues.some((entry) => entry.code === code), JSON.stringify(result.issues));
    }
});

test('normalization keeps a source node plot binding', async () => {
    const source = JSON.parse(await readFile(new URL('revision-a.source.json', fixtures), 'utf8'));
    source.root.plot = { slots: 8, channel: 'spectrum', style: 'bar' };
    const design = normalizeImport(source);
    assert.deepEqual(design.root.plot, { channel: 'spectrum', slots: 8, style: 'bar' });
    assert.deepEqual(validateDesignIR(design), { issues: [], valid: true });
});

test('the schema declares plot bindings without samples', async () => {
    const definitions = JSON.parse(await readFile(schema, 'utf8')).$defs;
    assert.deepEqual(definitions.node.properties.plot, { $ref: '#/$defs/plotBinding' });
    assert.equal(definitions.plotBinding.additionalProperties, false);
    assert.deepEqual(definitions.plotBinding.required, ['style', 'channel']);
    assert.equal(definitions.plotBinding.properties.samples, undefined);
    assert.equal(definitions.plotBinding.properties.slots.maximum, 4194304);
});
