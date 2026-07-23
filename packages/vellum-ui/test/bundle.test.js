import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
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
