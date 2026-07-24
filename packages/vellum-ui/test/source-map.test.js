import assert from 'node:assert/strict';
import {
    mkdirSync,
    mkdtempSync,
    readFileSync,
    writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import vm from 'node:vm';

import {
    blankSdkInstallPathComments,
    finalizeBundleSourceMap,
    normalizeSourceMap,
} from '../scripts/source-map.mjs';

const packageRoot = resolve(new URL('..', import.meta.url).pathname);
const repositoryRoot = resolve(packageRoot, '../..');
const phase3Entry = resolve(repositoryRoot, 'fixtures/authoring-phase3/src/App.tsx');

function prepareFixture() {
    const directory = mkdtempSync(join(tmpdir(), 'vellum-source-map-fixture-'));
    const fixture = join(directory, 'app');
    mkdirSync(join(fixture, 'src'), { recursive: true });
    writeFileSync(join(fixture, 'app.toml'), '[app]\nentry = "src/App.tsx"\n');
    // Preserve the governing fixture's exact throw location while keeping this
    // focused test independent of other still-pending Phase 3 capabilities.
    const prefix = Array.from({ length: 38 }, () => '').join('\n');
    writeFileSync(join(fixture, 'src/App.tsx'), `${prefix}
    const throwMappedError = () => {
        throw new Error('phase3-source-map-proof');
    };
    import { View, mount } from '@vellum/ui';
    function App() {
        return <View id="root">
            <button id="mapped-error" onPress={throwMappedError}>Throw mapped error</button>
        </View>;
    }
    mount(App);
`);
    return fixture;
}

function buildFixture(format) {
    const fixture = prepareFixture();
    const extension = format === 'esm' ? 'mjs' : 'js';
    const output = join(fixture, `build/app.${extension}`);
    const built = spawnSync(
        process.execPath,
        [resolve(packageRoot, 'scripts/build-project.mjs'),
            join(fixture, 'src/App.tsx'), output],
        {
            cwd: fixture,
            encoding: 'utf8',
            env: { ...process.env, VELLUM_BUILD_FORMAT: format },
        },
    );
    assert.equal(built.status, 0, built.stderr);
    return { fixture, output };
}

function mappedAction(bridge) {
    const root = JSON.parse(bridge.renderJSON()).tree;
    const pending = [root];
    while (pending.length > 0) {
        const node = pending.pop();
        if (node.id === 'mapped-error') return node.events.press;
        pending.push(...(node.children ?? []));
    }
    throw new Error('mapped-error action was not materialized');
}

function assertDiagnostic(value) {
    assert.equal(value.protocol, 'vellum.authoring-host.v2');
    assert.equal(value.kind, 'diagnostic');
    assert.equal(value.code, 'VELLUM_RUNTIME_EXCEPTION');
    assert.equal(value.message, 'phase3-source-map-proof');
    assert.equal(value.source.file, 'vellum://app/src/App.tsx');
    assert.equal(value.source.line, 40);
    assert.equal(value.source.column, 15);
    assert.equal(value.source.function, 'throwMappedError');
    assert.deepEqual(value.stack[0], value.source);
}

test('native IIFE maps the Phase 3 TSX exception to its exact source location', () => {
    assert.equal(
        readFileSync(phase3Entry, 'utf8').split('\n')[39],
        "        throw new Error('phase3-source-map-proof');",
    );
    const { output } = buildFixture('iife');
    const context = vm.createContext({
        clearTimeout() {},
        setTimeout() { return 1; },
    });
    vm.runInContext(readFileSync(output, 'utf8'), context, { filename: output });
    const action = mappedAction(context.__vellum);
    let thrown;
    try {
        context.__vellum.dispatchJSON(JSON.stringify({
            protocol: context.__vellum.protocol, action, payload: null,
        }));
    } catch (error) {
        thrown = error;
    }
    assert.ok(thrown);
    assertDiagnostic(JSON.parse(context.__vellumMapExceptionJSON(thrown)));
    assert.ok(
        readFileSync(`${output}.map`, 'utf8').includes('vellum://app/src/App.tsx'),
    );
});

test('browser ESM maps the same Phase 3 TSX exception', () => {
    const { fixture, output } = buildFixture('esm');
    const runner = join(fixture, 'run-mapped-error.mjs');
    writeFileSync(runner, `
      globalThis.setTimeout = () => 1;
      globalThis.clearTimeout = () => {};
      await import(${JSON.stringify(new URL(`file://${output}`).href)});
      const pending = [JSON.parse(globalThis.__vellum.renderJSON()).tree];
      let action = null;
      while (pending.length) {
        const node = pending.pop();
        if (node.id === 'mapped-error') { action = node.events.press; break; }
        pending.push(...(node.children || []));
      }
      try {
        globalThis.__vellum.dispatchJSON(JSON.stringify({
          protocol: globalThis.__vellum.protocol, action, payload: null,
        }));
      } catch (error) {
        process.stdout.write(globalThis.__vellumMapExceptionJSON(error));
      }
    `);
    const run = spawnSync(process.execPath, [runner], { encoding: 'utf8' });
    assert.equal(run.status, 0, run.stderr);
    assertDiagnostic(JSON.parse(run.stdout));
});

test('source-map loading fails closed for missing and malformed maps', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'vellum-source-map-invalid-'));
    const bundlePath = join(directory, 'app.js');
    writeFileSync(bundlePath, 'void 0;');
    await assert.rejects(
        finalizeBundleSourceMap({ bundlePath, projectRoot: directory, packageRoot }),
        /VELLUM_SOURCE_MAP_MISSING_OR_MALFORMED/,
    );
    writeFileSync(`${bundlePath}.map`, '{"version":3,"sources":[]}');
    await assert.rejects(
        finalizeBundleSourceMap({ bundlePath, projectRoot: directory, packageRoot }),
        /malformed source map/,
    );
    assert.throws(
        () => normalizeSourceMap({
            version: 3, sources: ['app.ts'], sourcesContent: [''],
            names: [], mappings: '!',
        }, { mapPath: `${bundlePath}.map`, projectRoot: directory, packageRoot }),
        /invalid base64 VLQ/,
    );
});

test('blanking SDK install-path comments preserves generated line geometry', () => {
    const posix = '// ../lib/vellum-installs/0.1.0-test-abc/ui/src/runtime.js';
    const windows = '// ..\\lib\\vellum-installs\\0.1.0-test-abc\\ui\\src\\design.js';
    const bundle = [
        posix,
        'var runtime = 1;',
        windows,
        'var design = 2;',
        '// src/App.tsx',
        '// an authored comment mentioning vellum-installs in prose',
        'export { runtime, design };',
        '',
    ].join('\n');

    const blanked = blankSdkInstallPathComments(bundle);
    const before = bundle.split('\n');
    const after = blanked.split('\n');

    assert.equal(after.length, before.length);
    assert.equal(blanked.includes('vellum-installs/0.1.0-test-abc'), false);
    assert.equal(blanked.includes('vellum-installs\\0.1.0-test-abc'), false);
    assert.equal(after[0], '');
    assert.equal(after[2], '');
    for (const index of [1, 3, 4, 5, 6, 7]) {
        assert.equal(after[index], before[index]);
    }
});

test('blanking SDK install-path comments keeps CRLF bundles line-aligned', () => {
    const bundle = [
        '// ../lib/vellum-installs/0.1.0-test-abc/ui/src/runtime.js',
        'var runtime = 1;',
        '',
    ].join('\r\n');

    const blanked = blankSdkInstallPathComments(bundle);

    assert.equal(blanked, ['', 'var runtime = 1;', ''].join('\r\n'));
});

test('finalizing a bundle blanks SDK install-path comments in the written output', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'vellum-source-map-relocatable-'));
    const bundlePath = join(directory, 'app.js');
    writeFileSync(bundlePath, [
        '// ../lib/vellum-installs/0.1.0-test-abc/ui/src/runtime.js',
        'var runtime = 1;',
        '// src/App.tsx',
        'var app = runtime;',
        '//# sourceMappingURL=app.js.map',
        '',
    ].join('\n'));
    writeFileSync(`${bundlePath}.map`, JSON.stringify({
        version: 3,
        sources: ['src/App.tsx'],
        sourcesContent: ['export const app = 1;\n'],
        names: [],
        mappings: 'AAAA',
    }));

    await finalizeBundleSourceMap({ bundlePath, projectRoot: directory, packageRoot });

    const written = readFileSync(bundlePath, 'utf8');
    assert.equal(written.includes('vellum-installs'), false);
    assert.deepEqual(written.split('\n').slice(0, 4), [
        '',
        'var runtime = 1;',
        '// src/App.tsx',
        'var app = runtime;',
    ]);
});
