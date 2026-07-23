import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
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
    const output = join(directory, 'imported.iife.js');
    writeFileSync(entry, `
        import { Design, mount } from '@vellum/ui';
        import { importedDesign } from '@vellum/imported';
        mount(() => <Design document={importedDesign} />);
    `);
    writeFileSync(design, JSON.stringify({
        source: { namespace: 'main' },
        tokens: { 'main.color.canvas': { $value: '#0f172a' } },
        root: {
            id: 'main/imported-root', kind: 'view', name: 'Imported Board',
            properties: { paint: { backgroundColor: '{color.canvas}' } },
            children: [{
                id: 'main/title', kind: 'text', text: 'Imported Board',
                properties: {}, children: [],
            }],
        },
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
    assert.equal(tree.children[0].children[0].text, 'Imported Board');
    assert.equal(tree.style.backgroundColor, '#0f172a');
    assert.match(bundle, /Imported Board/);
});
