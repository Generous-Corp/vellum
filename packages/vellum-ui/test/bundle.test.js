import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import vm from 'node:vm';

test('builds a DOM-free classic-script bundle for the native host', () => {
    const directory = mkdtempSync(join(tmpdir(), 'vellum-ui-bundle-'));
    const output = join(directory, 'native-app.iife.js');
    const build = spawnSync(
        process.execPath,
        ['scripts/build-native-test.mjs', output],
        { cwd: new URL('..', import.meta.url), encoding: 'utf8' },
    );
    assert.equal(build.status, 0, build.stderr);

    const context = vm.createContext({});
    vm.runInContext(readFileSync(output, 'utf8'), context, { filename: output });
    assert.equal(context.__vellum.protocol, 'vellum.authoring-host.v1');
    const tree = JSON.parse(context.__vellum.renderJSON()).tree;
    assert.equal(tree.id, 'native-proof');
    assert.equal(tree.children[1].children[0].text, 'Count ');
    assert.equal(tree.children[1].children[1].text, '0');
    const bundle = readFileSync(output, 'utf8');
    assert.equal(bundle.includes('document.'), false);
    assert.doesNotMatch(bundle, /^\/\/ .*ui\/src\//m);
});

test('bundles an external materialized design into the authored native path', () => {
    const directory = mkdtempSync(join(tmpdir(), 'vellum-ui-imported-bundle-'));
    const entry = join(directory, 'main.tsx');
    const design = join(directory, 'main.materialized.json');
    const bindings = join(directory, 'main.bindings.json');
    const output = join(directory, 'imported.iife.js');
    writeFileSync(entry, `
        import { Design, mount } from '@vellum/ui';
        import { importedBindings, importedDesign } from '@vellum/imported';
        globalThis.__bindingPresses = 0;
        function App() {
            return <Design document={importedDesign} bindings={importedBindings}
                actions={{ createBoard: () => { globalThis.__bindingPresses += 1; } }} />;
        }
        mount(App);
    `);
    writeFileSync(design, JSON.stringify({
        source: { key: 'main', namespace: 'main', revision: 'board-b' },
        tokens: { 'main.color.canvas': { $value: '#0f172a' } },
        root: {
            id: 'main/imported-root', kind: 'view', name: 'Imported Board',
            properties: { paint: { backgroundColor: '{color.canvas}' } },
            children: [{
                id: 'main/create', kind: 'button', text: 'Create board',
                properties: {}, children: [],
            }],
        },
    }));
    writeFileSync(bindings, JSON.stringify({
        schema: 'vellum.generated-bindings.v1',
        revision: 'board-b',
        sourceKey: 'main',
        bindings: [{
            action: 'createBoard',
            event: 'press',
            originalNodeId: 'main/create-old',
            resolvedNodeId: 'main/create',
        }],
    }));
    const build = spawnSync(
        process.execPath,
        ['scripts/build-project.mjs', entry, output, design],
        { cwd: new URL('..', import.meta.url), encoding: 'utf8' },
    );
    assert.equal(build.status, 0, build.stderr);
    const bundle = readFileSync(output, 'utf8');
    const context = vm.createContext({});
    vm.runInContext(bundle, context, { filename: output });
    const tree = JSON.parse(context.__vellum.renderJSON()).tree;
    assert.equal(tree.id, 'main/imported-root');
    assert.equal(tree.children[0].text, 'Create board');
    assert.equal(typeof tree.children[0].events.press, 'string');
    context.__vellum.dispatchJSON(JSON.stringify({
        action: tree.children[0].events.press,
        protocol: 'vellum.authoring-host.v1',
    }));
    assert.equal(context.__bindingPresses, 1);
    assert.equal(tree.style.backgroundColor, '#0f172a');
    assert.match(bundle, /Imported Board/);
    assert.match(bundle, /createBoard/);

    unlinkSync(bindings);
    const missingBindings = spawnSync(
        process.execPath,
        ['scripts/build-project.mjs', entry, output, design],
        { cwd: new URL('..', import.meta.url), encoding: 'utf8' },
    );
    assert.notEqual(missingBindings.status, 0);
    assert.match(missingBindings.stderr, /main\.bindings\.json/);
});

test('keeps wrapped and grid layout fields in the native render tree', () => {
    const directory = mkdtempSync(join(tmpdir(), 'vellum-ui-layout-bundle-'));
    const entry = join(directory, 'main.js');
    const design = join(directory, 'main.materialized.json');
    const bindings = join(directory, 'main.bindings.json');
    const output = join(directory, 'layout.iife.js');
    writeFileSync(entry, "import { Design, mount } from '@vellum/ui';\nimport { importedDesign } from '@vellum/imported';\nmount(() => Design({ document: importedDesign }));\n");
    writeFileSync(design, JSON.stringify({
        source: { key: 'main', revision: 'layout-b' },
        root: {
            id: 'main/root', kind: 'view', properties: {
                layout: {
                    display: 'grid', gridTemplateColumns: '1fr 1fr',
                    gridTemplateRows: 'auto auto', columnGap: 8, rowGap: 12,
                },
            }, children: [],
        },
    }));
    writeFileSync(bindings, '{}');
    const build = spawnSync(
        process.execPath, ['scripts/build-project.mjs', entry, output, design],
        { cwd: new URL('..', import.meta.url), encoding: 'utf8' },
    );
    assert.equal(build.status, 0, build.stderr);
    const context = vm.createContext({});
    vm.runInContext(readFileSync(output, 'utf8'), context, { filename: output });
    const tree = JSON.parse(context.__vellum.renderJSON()).tree;
    assert.equal(tree.style.direction, 'horizontal');
    assert.equal(tree.style.wrap, true);
    assert.equal(tree.style.gridTemplateColumns, '1fr 1fr');
    assert.equal(tree.style.rowGap, 12);
});


test('bundles referenced PNG bytes for the native host and verifies their digest', () => {
    const directory = mkdtempSync(join(tmpdir(), 'vellum-ui-native-png-'));
    const entry = join(directory, 'main.js');
    const generated = join(directory, 'ui', 'generated');
    const design = join(generated, 'main.materialized.json');
    const assetPath = join(directory, 'assets', 'generated', 'main', 'files', 'assets', 'pixel.png');
    const output = join(directory, 'app.js');
    const png = Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII=',
        'base64',
    );
    const hash = `sha256:${createHash('sha256').update(png).digest('hex')}`;
    mkdirSync(generated, { recursive: true });
    mkdirSync(join(assetPath, '..'), { recursive: true });
    writeFileSync(assetPath, png);
    writeFileSync(entry, 'export const app = 1;');
    writeFileSync(join(generated, 'main.bindings.json'), '{}');
    writeFileSync(design, JSON.stringify({
        assets: [{ mimeType: 'image/png', uri: 'assets/pixel.png', contentHash: hash }],
        root: { kind: 'image', properties: { image: { source: hash } }, children: [] },
    }));
    const build = () => spawnSync(process.execPath,
        ['scripts/build-project.mjs', entry, output, design],
        { cwd: new URL('..', import.meta.url), encoding: 'utf8', env: {
            ...process.env, VELLUM_BUILD_FORMAT: 'iife',
        } });
    const result = build();
    assert.equal(result.status, 0, result.stderr);
    assert.deepEqual(JSON.parse(readFileSync(`${output}.images.json`, 'utf8')),
        { [hash]: png.toString('base64') });
    writeFileSync(assetPath, 'changed');
    const mismatch = build();
    assert.notEqual(mismatch.status, 0);
    assert.match(mismatch.stderr, /does not match its content hash/);
});
