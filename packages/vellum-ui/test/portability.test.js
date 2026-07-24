import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import vm from 'node:vm';

import { checkPortability } from '../scripts/check-portability.mjs';

const fixtures = resolve(new URL('fixtures/portability', import.meta.url).pathname);

const expected = new Map([
    ['node-builtin.js', 'VELLUM_PORTABILITY_NODE_BUILTIN'],
    ['dom-global.js', 'VELLUM_PORTABILITY_DOM_GLOBAL'],
    ['dynamic-code.js', 'VELLUM_PORTABILITY_DYNAMIC_CODE'],
    ['dynamic-import.js', 'VELLUM_PORTABILITY_DYNAMIC_IMPORT'],
    ['undeclared-capability.js', 'VELLUM_PORTABILITY_UNDECLARED_CAPABILITY'],
    ['platform-import.js', 'VELLUM_PORTABILITY_PLATFORM_IMPORT'],
]);

for (const [fixture, code] of expected) {
    test(`stable diagnostic for ${fixture}`, async () => {
        const result = await checkPortability(join(fixtures, fixture), { target: 'web' });
        const repeated = await checkPortability(
            join(fixtures, fixture), { target: 'web' },
        );
        assert.deepEqual(repeated, result);
        assert.equal(result.schema, 'vellum.portability-diagnostics.v1');
        assert.equal(result.status, 'failed');
        assert.ok(result.diagnostics.some((item) => item.code === code), result);
        for (const item of result.diagnostics) {
            assert.deepEqual(Object.keys(item).slice(0, 5),
                ['code', 'file', 'line', 'column', 'message']);
        }
    });
}

test('build-project enforces portability before producing output', () => {
    const directory = mkdtempSync(join(tmpdir(), 'vellum-portability-build-'));
    const output = join(directory, 'invalid.iife.js');
    const run = spawnSync(process.execPath,
        ['scripts/build-project.mjs', join(fixtures, 'node-builtin.js'), output],
        { cwd: new URL('..', import.meta.url), encoding: 'utf8' });
    assert.equal(run.status, 1);
    const result = JSON.parse(run.stderr);
    assert.equal(result.schema, 'vellum.portability-diagnostics.v1');
    assert.equal(result.diagnostics[0].code, 'VELLUM_PORTABILITY_NODE_BUILTIN');
});

test('CLI emits one stable JSON result and fails closed', () => {
    const run = spawnSync(process.execPath,
        ['scripts/check-portability.mjs', join(fixtures, 'node-builtin.js'), 'native'],
        { cwd: new URL('..', import.meta.url), encoding: 'utf8' });
    assert.equal(run.status, 1);
    const result = JSON.parse(run.stdout);
    assert.equal(result.status, 'failed');
    assert.equal(result.diagnostics[0].code, 'VELLUM_PORTABILITY_NODE_BUILTIN');
});

test('transitive local pure-ESM packages build as native IIFE and web ESM', () => {
    const project = resolve(new URL('fixtures/pure-esm-project', import.meta.url).pathname);
    const directory = mkdtempSync(join(tmpdir(), 'vellum-pure-esm-'));
    const nativeOutput = join(directory, 'app.iife.js');
    const webOutput = join(directory, 'app.mjs');
    const cwd = new URL('..', import.meta.url);
    const nativeBuild = spawnSync(process.execPath,
        ['scripts/build-project.mjs', join(project, 'main.js'), nativeOutput],
        { cwd, encoding: 'utf8' });
    assert.equal(nativeBuild.status, 0, nativeBuild.stderr);
    const context = vm.createContext({});
    vm.runInContext(readFileSync(nativeOutput, 'utf8'), context);
    assert.equal(context.__pureEsmProof,
        'Hello, Vellum from a transitive pure-ESM package');

    const webBuild = spawnSync(process.execPath,
        ['scripts/build-project.mjs', join(project, 'main.js'), webOutput],
        { cwd, encoding: 'utf8', env: { ...process.env, VELLUM_BUILD_FORMAT: 'esm' } });
    assert.equal(webBuild.status, 0, webBuild.stderr);
    const web = readFileSync(webOutput, 'utf8');
    assert.match(web, /export\s*\{/);
    assert.match(web, /transitive pure-ESM package/);
});
