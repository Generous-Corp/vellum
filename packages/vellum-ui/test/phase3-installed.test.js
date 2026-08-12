import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
    cpSync,
    mkdtempSync,
    mkdirSync,
    readFileSync,
    rmSync,
    statSync,
    writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repository = resolve(packageRoot, '..', '..');
const fixture = join(repository, 'fixtures', 'authoring-phase3');

function run(command, arguments_, options = {}) {
    const result = spawnSync(command, arguments_, {
        encoding: 'utf8',
        ...options,
    });
    assert.equal(
        result.status,
        0,
        `${command} ${arguments_.join(' ')}\n${result.stdout}${result.stderr}`,
    );
    return result.stdout.trim();
}

test('packed SDK builds and executes the unchanged Phase 3 fixture in both formats', async () => {
    const temporary = mkdtempSync(join(tmpdir(), 'vellum-phase3-installed-'));
    try {
        const app = join(temporary, 'app');
        const packages = join(temporary, 'packages');
        mkdirSync(packages);
        cpSync(fixture, app, { recursive: true });
        const packed = JSON.parse(run(
            'npm',
            ['pack', packageRoot, '--pack-destination', packages, '--json'],
        ));
        const archive = join(packages, packed[0].filename);
        if (process.platform !== 'win32') {
            const bundledBinary = statSync(
                join(packageRoot, 'node_modules', 'esbuild', 'bin', 'esbuild'),
            );
            const platformBinary = statSync(join(
                packageRoot,
                'node_modules',
                '@esbuild',
                `${process.platform}-${process.arch}`,
                'bin',
                'esbuild',
            ));
            if (bundledBinary.ino !== 0 && platformBinary.ino !== 0) {
                assert.notEqual(bundledBinary.ino, platformBinary.ino);
            }
        }
        const manifestPath = join(app, 'package.json');
        const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
        manifest.dependencies['@vellum/ui'] = `file:${archive}`;
        writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
        run('npm', [
            'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund',
            '--package-lock=false', '--prefix', app,
        ]);

        const installed = join(app, 'node_modules', '@vellum', 'ui');
        const entry = join(app, 'src', 'App.tsx');
        const native = join(app, 'build', 'app.js');
        const browser = join(app, 'build', 'app.mjs');
        const buildScript = join(installed, 'scripts', 'build-project.mjs');
        run(process.execPath, [buildScript, entry, native], {
            env: {
                ...process.env,
                VELLUM_BUILD_FORMAT: 'iife',
                VELLUM_PROJECT_ROOT: app,
            },
        });
        run(process.execPath, [buildScript, entry, browser], {
            env: {
                ...process.env,
                VELLUM_BUILD_FORMAT: 'esm',
                VELLUM_PROJECT_ROOT: app,
            },
        });

        const digest = createHash('sha256').update(readFileSync(entry)).digest('hex');
        const gate = JSON.parse(readFileSync(join(app, 'gate-manifest.json'), 'utf8'));
        assert.equal(digest, gate.application.sourceSha256);
        assert.ok(readFileSync(native).length > 1000);
        const requests = [];
        globalThis.__vellumServiceHost = {
            capabilities: {
                commands: 'v1',
                files: 'denied',
                clipboard: 'text-v1',
                open_url: 'external-v1',
                persistence: 'state-v1',
            },
            request(request) {
                requests.push(request);
                return Promise.resolve({
                    protocol: 'vellum.services.v1',
                    kind: 'response',
                    id: request.id,
                    ok: true,
                    value: null,
                });
            },
        };
        await import(`${pathToFileURL(browser).href}?proof=${Date.now()}`);
        let tree = JSON.parse(globalThis.__vellum.renderJSON()).tree;
        assert.equal(tree.id, 'phase3-app');
        await new Promise((resolve_) => setTimeout(resolve_, 20));
        tree = JSON.parse(globalThis.__vellum.pumpJSON()).tree;
        const find = (node, id) => node.id === id
            ? node
            : (node.children ?? []).map((child) => find(child, id)).find(Boolean);
        assert.equal(find(tree, 'status').children[0].text, 'timer-complete');
        const add = find(tree, 'phase3/imported-add').events.press;
        tree = JSON.parse(globalThis.__vellum.dispatchJSON(JSON.stringify({
            protocol: 'vellum.authoring-host.v1',
            action: add,
            payload: { pointerType: 'touch', x: 20, y: 20 },
        }))).tree;
        assert.match(
            JSON.stringify(find(tree, 'item-list')),
            /Board: Roadmap/,
        );
        const snapshot = globalThis.__vellum.snapshotStateJSON();
        globalThis.__vellum.dispatchJSON(JSON.stringify({
            protocol: 'vellum.authoring-host.v1',
            action: add,
            payload: null,
        }));
        tree = JSON.parse(globalThis.__vellum.restoreStateJSON(snapshot)).tree;
        assert.match(JSON.stringify(find(tree, 'item-list')), /Board: Roadmap/);
        assert.equal(
            JSON.stringify(find(tree, 'item-list')).match(/Board: Roadmap/g)?.length,
            1,
        );

        for (const target of ['copy', 'docs']) {
            const action = find(tree, target).events.press;
            globalThis.__vellum.dispatchJSON(JSON.stringify({
                protocol: 'vellum.authoring-host.v1',
                action,
                payload: null,
            }));
            await new Promise((resolve_) => setImmediate(resolve_));
            tree = JSON.parse(globalThis.__vellum.pumpJSON()).tree;
        }
        assert.deepEqual(
            requests.map(({ service, operation }) => ({ service, operation })),
            [
                { service: 'clipboard', operation: 'writeText' },
                { service: 'open_url', operation: 'openExternal' },
            ],
        );
        assert.equal(find(tree, 'status').children[0].text, 'url-complete');

        globalThis.__vellumServiceHost = {
            capabilities: { clipboard: 'denied', open_url: 'denied' },
            request() {
                throw new Error('denied services must not invoke their provider');
            },
        };
        const deniedCopy = find(tree, 'copy').events.press;
        globalThis.__vellum.dispatchJSON(JSON.stringify({
            protocol: 'vellum.authoring-host.v1',
            action: deniedCopy,
            payload: null,
        }));
        await new Promise((resolve_) => setImmediate(resolve_));
        tree = JSON.parse(globalThis.__vellum.pumpJSON()).tree;
        assert.equal(
            find(tree, 'status').children[0].text,
            'clipboard-capability-denied',
        );
        const mapped = find(tree, 'mapped-error').events.press;
        assert.throws(() => globalThis.__vellum.dispatchJSON(JSON.stringify({
            protocol: 'vellum.authoring-host.v1',
            action: mapped,
            payload: null,
        })), /phase3-source-map-proof/);
    } finally {
        delete globalThis.__vellum;
        delete globalThis.__vellumServiceHost;
        rmSync(temporary, { recursive: true, force: true });
    }
});
