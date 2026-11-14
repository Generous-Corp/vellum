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

function withMaterial(design, material) {
    const copy = structuredClone(design);
    copy.root.material = material;
    return copy;
}

test('a node references a material by id and carries values only', async () => {
    const design = await document();
    const glass = {
        id: 'vellum.frosted-glass',
        values: { blur: 12, offset: [1, 2], tint: { red: 1, green: 1, blue: 1, alpha: 0.2 } },
    };
    assert.deepEqual(validateDesignIR(withMaterial(design, glass)), { issues: [], valid: true });
});

test('a material reference refuses declarations and malformed values', async () => {
    const design = await document();
    const cases = [
        [{ id: 'vellum.frosted-glass', values: {}, uniforms: [] }, 'unknown-field'],
        [{ id: 'Vellum Glass', values: {} }, 'format'],
        [{ id: 'vellum.frosted-glass' }, 'type'],
        [{ id: 'vellum.frosted-glass', values: { blur: '12' } }, 'type'],
        [{ id: 'vellum.frosted-glass', values: { blur: [1, 2, 3] } }, 'type'],
        [{ id: 'vellum.frosted-glass', values: { tint: { red: 1, green: 1, blue: 1 } } }, 'type'],
        [{ id: 'vellum.frosted-glass', values: { 'two words': 1 } }, 'format'],
    ];
    for (const [material, code] of cases) {
        const result = validateDesignIR(withMaterial(design, material));
        assert.equal(result.valid, false, JSON.stringify(material));
        assert.ok(result.issues.some((entry) => entry.code === code), JSON.stringify(result.issues));
    }
});

test('normalization keeps a source node material', async () => {
    const source = JSON.parse(await readFile(new URL('revision-a.source.json', fixtures), 'utf8'));
    source.root.material = { values: { blur: 4 }, id: 'vellum.frosted-glass' };
    const design = normalizeImport(source);
    assert.deepEqual(design.root.material, { id: 'vellum.frosted-glass', values: { blur: 4 } });
    assert.deepEqual(validateDesignIR(design), { issues: [], valid: true });
});

test('the schema declares material references with values only', async () => {
    const definitions = JSON.parse(await readFile(schema, 'utf8')).$defs;
    assert.deepEqual(definitions.node.properties.material, { $ref: '#/$defs/materialReference' });
    assert.deepEqual(Object.keys(definitions.materialReference.properties), ['id', 'values']);
    assert.equal(definitions.materialReference.additionalProperties, false);
});
