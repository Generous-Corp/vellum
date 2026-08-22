import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
    indexTree,
    normalizeImport,
    parseReimportReport,
    reimportDesign,
    stableStringify,
    validateAuthoredOverlay,
} from '../src/index.js';

const fixtures = new URL('../../../fixtures/design-ir/', import.meta.url);

async function fixture(name) {
    return JSON.parse(await readFile(new URL(name, fixtures), 'utf8'));
}

async function importPair() {
    return {
        previous: normalizeImport(await fixture('revision-a.source.json')),
        next: normalizeImport(await fixture('revision-b.source.json')),
    };
}

test('A-to-B reimport preserves authored behavior and matches the checked-in manifest', async () => {
    const { previous, next } = await importPair();
    const overlay = await fixture('authored.overlay.json');
    const expected = await fixture('expected.reimport-manifest.json');
    const previousBefore = stableStringify(previous);
    const nextBefore = stableStringify(next);
    const overlayBefore = stableStringify(overlay);

    const result = reimportDesign(previous, next, overlay);

    assert.equal(result.accepted, true);
    assert.equal(stableStringify(previous), previousBefore);
    assert.equal(stableStringify(next), nextBefore);
    assert.equal(stableStringify(overlay), overlayBefore);
    assert.equal(result.report.authoredOverlay.preservedByteForByte, true);
    assert.deepEqual(parseReimportReport(stableStringify(result.report)), result.report);
    assert.deepEqual(
        result.report.changes.changed.find(({ id }) => id === 'main/card-a').fields,
        ['properties.paint.borderRadius'],
    );

    const projection = {
        added: result.report.changes.added.map(({ id }) => id),
        aliases: result.report.changes.aliases,
        bindings: result.resolvedBindings.map(({ action, originalNodeId, resolvedNodeId }) => ({
            action,
            originalNodeId,
            resolvedNodeId,
        })),
        heuristicCandidates: result.report.heuristicCandidates,
        moved: result.report.changes.moved,
        removed: result.report.changes.removed.map(({ id }) => id),
        retained: result.report.changes.retained.map(({ id }) => id),
        summary: result.report.summary,
        tokenLayers: result.tokenLayers,
    };
    assert.deepEqual(projection, expected);

    const canonicalCard = indexTree(next.root).index.get('main/card-a').node;
    const materializedCard = indexTree(result.materialized.root).index.get('main/card-a').node;
    assert.equal(canonicalCard.properties.paint.borderRadius, 12);
    assert.equal(materializedCard.properties.paint.borderRadius, 20);
    assert.equal(result.resolvedBindings[0].handlerModule, 'src/commands/boards.ts');
});

test('an authored reference to a removed node blocks acceptance', async () => {
    const { previous, next } = await importPair();
    const overlay = await fixture('authored-with-orphan.overlay.json');
    const result = reimportDesign(previous, next, overlay);

    assert.equal(result.accepted, false);
    assert.equal(result.report.summary.conflicts, 1);
    assert.deepEqual(
        result.report.conflicts.map(({ code, kind, nodeId }) => ({ code, kind, nodeId })),
        [{ code: 'identity-removed', kind: 'binding', nodeId: 'main/legacy-tip' }],
    );
    assert.equal(
        result.resolvedBindings.some((binding) => binding.action === 'boards.create'),
        true,
        'unrelated authored behavior remains available for review even though commit is blocked',
    );
});

test('alias cycles fail closed and never guess a target', async () => {
    const { previous, next } = await importPair();
    const overlay = await fixture('authored.overlay.json');
    overlay.aliases = {
        'main/create-button-v1': 'main/missing-a',
        'main/missing-a': 'main/create-button-v1',
    };
    const result = reimportDesign(previous, next, overlay);
    assert.equal(result.accepted, false);
    assert.equal(result.report.conflicts.some(({ code }) => code === 'alias-cycle'), true);
});

test('an unused reviewed alias with a missing target still blocks acceptance', async () => {
    const { previous, next } = await importPair();
    const overlay = await fixture('authored.overlay.json');
    overlay.aliases['main/legacy-tip'] = 'main/missing-tip';
    const result = reimportDesign(previous, next, overlay);
    assert.equal(result.accepted, false);
    assert.equal(result.report.conflicts.some(({ code }) => code === 'alias-target-missing'), true);
});

test('a newly introduced unsupported conversion blocks reimport by default', async () => {
    const { previous } = await importPair();
    const overlay = await fixture('authored.overlay.json');
    const nextSource = await fixture('revision-b.source.json');
    nextSource.diagnostics.push({
        code: 'mask-unsupported',
        disposition: 'unsupported',
        message: 'Complex masks require a custom component',
        path: '$.root.children[2]',
        severity: 'warning',
    });
    const next = normalizeImport(nextSource);
    const result = reimportDesign(previous, next, overlay);
    assert.equal(result.accepted, false);
    assert.equal(result.report.conflicts.at(-1).code, 'new-conversion-loss');

    const explicitlyReviewed = reimportDesign(previous, next, overlay, {
        requireNoNewLosses: false,
    });
    assert.equal(explicitlyReviewed.accepted, true);
});

test('source keys, namespaces, and adapters cannot drift during reimport', async () => {
    const { previous, next } = await importPair();
    const overlay = await fixture('authored.overlay.json');
    next.source.namespace = 'renamed';
    assert.throws(() => reimportDesign(previous, next, overlay), /cannot change source.namespace/);
});

test('authored overlays cannot escape their source namespace or properties boundary', async () => {
    const overlay = await fixture('authored.overlay.json');
    overlay.bindings[0].nodeId = 'another/create-button-v1';
    overlay.overrides[0].path = 'properties.__proto__.polluted';
    const validation = validateAuthoredOverlay(overlay);
    assert.equal(validation.valid, false);
    assert.deepEqual(
        validation.issues.map(({ code }) => code).sort(),
        ['namespace', 'ownership-boundary'],
    );
    assert.equal(Object.prototype.polluted, undefined);
});

test('authored overrides replace inherited names with owned data properties', async () => {
    const { previous, next } = await importPair();
    const overlay = await fixture('authored.overlay.json');
    overlay.overrides[0] = {
        nodeId: 'main/card-a',
        path: 'properties.toString.value',
        value: 42,
    };

    const result = reimportDesign(previous, next, overlay);
    const materializedCard = indexTree(result.materialized.root).index.get('main/card-a').node;

    assert.equal(result.accepted, true);
    assert.equal(Object.hasOwn(materializedCard.properties, 'toString'), true);
    assert.deepEqual(materializedCard.properties.toString, { value: 42 });
    assert.equal(Object.prototype.value, undefined);
});
