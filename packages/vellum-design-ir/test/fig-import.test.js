import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { decodeFigFile } from '../src/fig-file/import.js';
import { loadSource } from '../bin/backend-source.js';
import { validateDesignIR } from '../src/index.js';
import * as zlib from 'node:zlib';
import { makeSyntheticFig } from './support/make_synthetic_fig.mjs';
import { decompressChunk } from '../src/fig-file/container.mjs';
import { familyMatchesStem } from '../src/fig-file/fonts.mjs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

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

test('a default import persists the resolved frame GUID so reimport pins it', async () => {
    const loaded = await loadSource(fixture.pathname, { sourceArchive: null, sourceKey: 'main', sourceType: 'fig' });
    assert.match(loaded.sourceArtifact.frame, /^\d+:\d+$/);
    assert.equal(Object.hasOwn(loaded, 'frame'), false);
    const pinned = await loadSource(fixture.pathname, {
        figFrame: loaded.sourceArtifact.frame, sourceArchive: null, sourceKey: 'main', sourceType: 'fig',
    });
    assert.deepEqual(pinned.document, loaded.document);
    assert.equal(pinned.sourceArtifact.frame, loaded.sourceArtifact.frame);
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

test('a fractional Figma font size survives the import', async () => {
    const document = decodeFigFile(await readFile(fixture), { sourceKey: 'main', frame: '0:2' });
    const sizes = [];
    const walk = (node) => {
        if (node && typeof node === 'object') {
            if (typeof node.fontSize === 'number') sizes.push(node.fontSize);
            for (const value of Object.values(node)) walk(value);
        }
    };
    walk(document);
    assert.ok(sizes.includes(12.5), `font sizes seen: ${JSON.stringify(sizes)}`);
});

test('the offline decoder entrypoint runs against the fixture', () => {
    const entry = fileURLToPath(new URL('../src/fig-file/fig_decode.mjs', import.meta.url));
    const run = spawnSync(process.execPath, [entry, 'outline', fileURLToPath(fixture)], { encoding: 'utf8' });
    assert.equal(run.status, 0, run.stderr);
    assert.ok(run.stdout.length > 0);
});

test('a font family matches its own faces, not a different family that shares its prefix', () => {
    assert.equal(familyMatchesStem('Roboto', 'Roboto'), true);
    assert.equal(familyMatchesStem('Roboto', 'Roboto-Bold'), true);
    assert.equal(familyMatchesStem('Roboto', 'RobotoMedium-Italic'), true);
    assert.equal(familyMatchesStem('Inter', 'Inter[opsz,wght]'), true);
    assert.equal(familyMatchesStem('Roboto', 'RobotoCondensed-Regular'), false);
    assert.equal(familyMatchesStem('Roboto', 'RobotoMono'), false);
});

test('offline instance text overrides render as text without audio metadata', () => {
    const document = decodeFigFile(makeSyntheticFig({ instance: true }));
    const instance = document.root.children.find((node) => node.name === 'Badge instance');
    assert.equal(instance.children[0].text, 'Ready');
});

test('offline loss diagnostics keep the source code and message', async () => {
    const document = decodeFigFile(await readFile(fixture), { frame: '0:10' });
    const diagnostic = document.diagnostics.find(({ code }) => code === 'unsupported-paint-type');
    assert.equal(diagnostic?.disposition, 'unsupported');
    assert.match(diagnostic.message, /VIDEO/);
});

test('offline layout preserves solved positions across grid and wrapped rows', () => {
    for (const layoutMode of ['grid', 'wrap']) {
        const document = decodeFigFile(makeSyntheticFig({ layoutMode }));
        const row = document.root.children.find(({ identity }) => identity.sourceId === '0:6');
        assert.equal(row.properties.layout.display, layoutMode === 'grid' ? 'grid' : 'flex');
        assert.deepEqual(row.children.map(({ properties: { layout } }) => [layout.x, layout.y]),
            [[4, 8], [36, 8], [4, 40]]);
    }
});

test('offline PNG paint references the hash of its embedded bytes', async () => {
    const bytes = await readFile(fixture);
    const document = decodeFigFile(bytes, { frame: '0:10' });
    const image = document.root.children.find(({ identity }) => identity.sourceId === '0:12');
    assert.equal(image.kind, 'image');
    assert.match(image.properties.image.source, /^sha256:[0-9a-f]{64}$/);
    assert.equal(image.properties.image.source, document.assets[0].contentHash);
    assert.equal(image.properties.image.fit, 'contain');
    assert.equal(image.compositing.opacity, 0.5);
});
