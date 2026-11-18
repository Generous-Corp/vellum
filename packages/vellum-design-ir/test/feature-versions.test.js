import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
    claimedListProblems,
    expectedOutputs,
    parseTable,
    sourcePaths,
} from '../../../tools/versions/generate_feature_versions.mjs';
import {
    DESIGN_IR_COMPOSITING_VERSION,
    DESIGN_IR_LATEST_VERSION,
    DESIGN_IR_MATERIALS_VERSION,
    DESIGN_IR_PLOTS_VERSION,
    DESIGN_IR_TRACKS_VERSION,
    DESIGN_IR_VECTOR_ASSET_VERSION,
    DESIGN_IR_VERSIONS,
    normalizeImport,
    validateDesignIR,
} from '../src/index.js';
import { FEATURE_VERSIONS } from '../src/feature-versions.generated.js';

const { header, sceneHeader } = sourcePaths();
const rows = parseTable(readFileSync(header, 'utf8'));
const source = { key: 'main', adapter: 'svg', adapterVersion: '1', formatVersion: '1.1', revision: 'test' };

test('the generated feature version modules match feature_versions.hpp', () => {
    for (const [path, text] of expectedOutputs()) {
        assert.equal(readFileSync(path, 'utf8'), text,
            `${path} drifted; run node tools/versions/generate_feature_versions.mjs`);
    }
    assert.deepEqual(FEATURE_VERSIONS, rows);
});

test('scene.hpp claims exactly the scene versions of the table', () => {
    assert.deepEqual(claimedListProblems(rows, readFileSync(sceneHeader, 'utf8')), []);
});

test('the DesignIR constants, version list, types and schema follow the table', () => {
    const designIr = rows.filter((row) => row.designIrVersion !== 0);
    const byName = (name) => rows.find((row) => row.name === name).designIrVersion;
    assert.equal(DESIGN_IR_COMPOSITING_VERSION, byName('compositing'));
    assert.equal(DESIGN_IR_MATERIALS_VERSION, byName('effects'));
    assert.equal(DESIGN_IR_TRACKS_VERSION, byName('tracks'));
    assert.equal(DESIGN_IR_PLOTS_VERSION, byName('plots'));
    assert.equal(DESIGN_IR_VECTOR_ASSET_VERSION, byName('vectors'));
    const expected = [1, ...designIr.map((row) => row.designIrVersion).sort((a, b) => a - b)];
    assert.deepEqual([...DESIGN_IR_VERSIONS], expected);
    assert.equal(DESIGN_IR_LATEST_VERSION, expected.at(-1));

    const declarations = readFileSync(
        fileURLToPath(new URL('../src/index.d.ts', import.meta.url)), 'utf8');
    assert.ok(declarations.includes(`export const DESIGN_IR_VERSIONS: readonly [${expected.join(', ')}];`));
    assert.ok(declarations.includes(`export const DESIGN_IR_LATEST_VERSION: ${expected.at(-1)};`));

    const schema = JSON.parse(readFileSync(
        fileURLToPath(new URL('../schema/design-ir-v1.schema.json', import.meta.url)), 'utf8'));
    assert.deepEqual(schema.properties.schemaVersion.enum, expected);
    const gates = new Map(schema.allOf.map((rule) => {
        const condition = rule.if.properties.schemaVersion;
        const lastRefused = condition.const !== undefined ? 1 : Math.max(...condition.enum);
        return [rule.then.properties.root.$ref, lastRefused + 1];
    }));
    const definitions = {
        tracks: '#/$defs/nodeWithoutTracks',
        compositing: '#/$defs/nodeWithoutCompositing',
        effects: '#/$defs/nodeWithoutMaterial',
        plots: '#/$defs/nodeWithoutPlot',
        vectors: '#/$defs/nodeBeforeVectorAssets',
    };
    assert.equal(gates.size, designIr.length);
    for (const row of designIr) {
        assert.equal(gates.get(definitions[row.name]), row.designIrVersion,
            `the schema gate for ${row.name} starts at the wrong version`);
    }
});

const FIELD_BUILDERS = {
    compositing: (root) => { root.compositing = { opacity: 0.5 }; },
    effects: (root) => { root.material = {}; },
    tracks: (root) => { root.tracks = []; },
    plots: (root) => { root.plot = {}; },
    vectors: (root) => { root.kind = 'vector'; },
};

function versionIssues(version, build) {
    const document = normalizeImport({ source, root: { kind: 'view', sourceId: 'root', children: [] } });
    build(document.root);
    document.schemaVersion = version;
    return validateDesignIR(document).issues.filter((issue) =>
        issue.code === 'version' || (issue.path === '$.schemaVersion' && /schemaVersion/.test(issue.message)));
}

test('every DesignIR row has a validator gate that refuses its field one version early', () => {
    for (const row of rows.filter((entry) => entry.designIrVersion !== 0)) {
        const build = FIELD_BUILDERS[row.name];
        assert.ok(build, `feature ${row.name} has no field builder in this test`);
        assert.ok(versionIssues(row.designIrVersion - 1, build).length > 0,
            `${row.name}: no validator gate refuses its field at schemaVersion ${row.designIrVersion - 1}`);
        assert.deepEqual(versionIssues(row.designIrVersion, build), [],
            `${row.name}: its field is refused at schemaVersion ${row.designIrVersion}`);
    }
});
