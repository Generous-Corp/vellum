import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs';
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
    assert.equal(readFileSync(output, 'utf8').includes('document.'), false);
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
