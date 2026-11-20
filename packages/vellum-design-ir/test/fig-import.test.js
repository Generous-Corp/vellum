import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { decodeFigFile } from '../src/fig-file/import.js';
import { loadSource } from '../bin/backend-source.js';
import { validateDesignIR } from '../src/index.js';
import * as zlib from 'node:zlib';
import { makeSyntheticFig } from './support/make_synthetic_fig.mjs';
import { decompressChunk } from '../src/fig-file/container.mjs';

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

test('a zstd-compressed .fig decodes where Node has zstd, and is refused clearly where it does not', () => {
    if (typeof zlib.zstdCompressSync === 'function') {
        const document = decodeFigFile(makeSyntheticFig({ compression: 'zstd' }), { sourceKey: 'main', frame: '0:2' });
        assert.equal(validateDesignIR(document).valid, true);
        assert.equal(document.root.name, 'Plugin UI');
    } else {
        // Without zstd in Node, a zstd chunk is refused with the version it needs.
        assert.throws(() => decompressChunk(Buffer.from([0x28, 0xb5, 0x2f, 0xfd, 0, 0])), /Node\.js 22\.15 or newer/);
    }
});
