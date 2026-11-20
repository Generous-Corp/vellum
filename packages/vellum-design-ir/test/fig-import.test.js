import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { decodeFigFile } from '../src/figma/import.js';
import { loadSource } from '../bin/backend-source.js';
import { validateDesignIR } from '../src/index.js';

const fixture = new URL('./fixtures/synthetic.fig', import.meta.url);

test('decodes the committed .fig fixture into valid DesignIR', async () => {
    const bytes = await readFile(fixture);
    const document = decodeFigFile(bytes, { sourceKey: 'main', frame: '0:2' });
    assert.equal(validateDesignIR(document).valid, true);
    assert.equal(document.source.adapter, 'figma-plugin');
    assert.equal(document.source.key, 'main');
    assert.equal(document.root.name, 'Plugin UI');
    assert.equal(document.root.children.some((node) => node.kind === 'text'), true);
});

test('backend accepts .fig as a local source without archive dispatch', async () => {
    const loaded = await loadSource(fixture.pathname, { sourceArchive: null, sourceKey: 'main', sourceType: 'fig' });
    assert.equal(loaded.sourceArtifact.kind, 'fig');
    assert.equal(loaded.document.source.key, 'main');
});
