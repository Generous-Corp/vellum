#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { build } from 'esbuild';

const SCHEMA = 'vellum.portability-diagnostics.v1';
const PACKAGE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BUILTINS = /^(?:node:)?(?:assert|buffer|child_process|cluster|crypto|dgram|dns|events|fs|http|https|module|net|os|path|perf_hooks|process|readline|stream|string_decoder|timers|tls|tty|url|util|v8|vm|worker_threads|zlib)(?:\/|$)/;
const IMPORTS = /(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]|import\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
const SERVICE = /^@vellum\/services\/([a-z][a-z0-9_-]*)$/;
const PLATFORM = /(?:^|\/)platforms\/(macos|windows|ios|android|web)(?:\/|$)/;
const DOM_GLOBALS = /\b(window|navigator|HTMLElement|localStorage|sessionStorage)\b|(\bdocument)\s*\.\s*(?:getElementById|querySelector|createElement|body|head|addEventListener)\b/g;
const DYNAMIC_CODE = /\b(eval|Function)\s*\(/g;
const DYNAMIC_IMPORT = /\bimport\s*\(\s*(?!['"])/g;

function lineColumn(source, offset) {
    const before = source.slice(0, offset);
    const lines = before.split('\n');
    return { line: lines.length, column: lines.at(-1).length + 1 };
}

function diagnostic(code, file, source, offset, message, detail = {}) {
    return { code, file, ...lineColumn(source, offset), message, ...detail };
}

function maskCommentsAndStrings(source) {
    return source.replace(
        /\/\*[\s\S]*?\*\/|\/\/[^\n]*|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|`(?:\\.|[^`\\])*`/g,
        (match) => match.replace(/[^\n]/g, ' '),
    );
}

function maskComments(source) {
    return source.replace(
        /\/\*[\s\S]*?\*\/|\/\/[^\n]*/g,
        (match) => match.replace(/[^\n]/g, ' '),
    );
}

async function findManifest(entry) {
    let directory = dirname(entry);
    while (true) {
        const candidate = resolve(directory, 'app.toml');
        try {
            return await readFile(candidate, 'utf8');
        } catch (error) {
            if (error.code !== 'ENOENT') throw error;
        }
        const parent = dirname(directory);
        if (parent === directory) return '';
        directory = parent;
    }
}

function capabilityEnabled(manifest, name) {
    const lines = manifest.split('\n');
    const start = lines.findIndex((line) => line.trim() === '[capabilities]');
    if (start < 0) return false;
    const endOffset = lines.slice(start + 1).findIndex(
        (line) => line.trim().startsWith('['),
    );
    const end = endOffset < 0 ? lines.length : start + 1 + endOffset;
    const section = lines.slice(start + 1, end).find((line) =>
        line.trim().startsWith(`${name} =`));
    const value = section?.split('=', 2)[1]?.split('#', 1)[0]?.trim();
    return value === 'true' || (value?.startsWith('"') && value !== '"none"');
}

export async function checkPortability(entryPath, { target = 'portable' } = {}) {
    const entry = resolve(entryPath);
    const root = dirname(entry);
    const manifest = await findManifest(entry);
    const diagnostics = [];
    const scanned = new Set();
    const plugin = {
        name: 'vellum-portability',
        setup(context) {
            context.onResolve(
                { filter: /^@vellum\/(?:imported|services\/)/ },
                (args) => ({ path: args.path, external: true }),
            );
            context.onResolve({ filter: BUILTINS }, (args) => {
                return { path: args.path, external: true };
            });
            context.onLoad({ filter: /\.[cm]?[jt]sx?$/ }, async (args) => {
                if (scanned.has(args.path)) return null;
                scanned.add(args.path);
                const source = await readFile(args.path, 'utf8');
                const executable = maskCommentsAndStrings(source);
                const uncommented = maskComments(source);
                const file = relative(root, args.path) || '.';
                for (const match of executable.matchAll(DOM_GLOBALS)) {
                    diagnostics.push(diagnostic(
                        'VELLUM_PORTABILITY_DOM_GLOBAL', file, source, match.index,
                        `DOM global is unavailable in the native runtime: ${match[1] ?? match[2]}`,
                        { global: match[1] ?? match[2] },
                    ));
                }
                for (const match of executable.matchAll(DYNAMIC_CODE)) {
                    diagnostics.push(diagnostic(
                        'VELLUM_PORTABILITY_DYNAMIC_CODE', file, source, match.index,
                        `Dynamic code execution is unsupported: ${match[1]}`,
                        { construct: match[1] },
                    ));
                }
                for (const match of executable.matchAll(DYNAMIC_IMPORT)) {
                    diagnostics.push(diagnostic(
                        'VELLUM_PORTABILITY_DYNAMIC_IMPORT', file, source, match.index,
                        'Dynamic import requires a static string specifier',
                    ));
                }
                for (const match of uncommented.matchAll(IMPORTS)) {
                    const specifier = match[1] ?? match[2];
                    if (BUILTINS.test(specifier)) {
                        diagnostics.push(diagnostic(
                            'VELLUM_PORTABILITY_NODE_BUILTIN',
                            file, source, match.index,
                            `Node builtin is not portable: ${specifier}`,
                            { specifier },
                        ));
                    }
                    const service = specifier.match(SERVICE);
                    if (service && !capabilityEnabled(manifest, service[1])) {
                        diagnostics.push(diagnostic(
                            'VELLUM_PORTABILITY_UNDECLARED_CAPABILITY',
                            file, source, match.index,
                            `Service import requires [capabilities].${service[1]}`,
                            { capability: service[1], specifier },
                        ));
                    }
                    const platform = specifier.match(PLATFORM);
                    if (platform && platform[1] !== target) {
                        diagnostics.push(diagnostic(
                            'VELLUM_PORTABILITY_PLATFORM_IMPORT',
                            file, source, match.index,
                            `Platform-only import '${platform[1]}' is invalid for '${target}'`,
                            { platform: platform[1], specifier, target },
                        ));
                    }
                }
                return null;
            });
        },
    };
    try {
        await build({
            entryPoints: [entry], bundle: true, write: false, platform: 'neutral',
            format: target === 'web' ? 'esm' : 'iife', logLevel: 'silent',
            jsx: 'automatic', jsxImportSource: '@vellum/ui',
            alias: {
                '@vellum/ui': resolve(PACKAGE_ROOT, 'src/index.js'),
                '@vellum/ui/jsx-runtime': resolve(PACKAGE_ROOT, 'src/jsx-runtime.js'),
                '@vellum/ui/jsx-dev-runtime': resolve(PACKAGE_ROOT, 'src/jsx-runtime.js'),
            },
            plugins: [plugin],
        });
    } catch (error) {
        for (const item of error.errors ?? []) {
            diagnostics.push({
                code: 'VELLUM_PORTABILITY_RESOLUTION',
                file: item.location?.file ?? relative(root, entry),
                line: item.location?.line ?? 1,
                column: item.location?.column ? item.location.column + 1 : 1,
                message: item.text,
            });
        }
    }
    diagnostics.sort((a, b) =>
        a.file.localeCompare(b.file) || a.line - b.line ||
        a.column - b.column || a.code.localeCompare(b.code));
    return { schema: SCHEMA, status: diagnostics.length ? 'failed' : 'passed',
        target, entry: relative(root, entry), diagnostics };
}

async function main() {
    const args = process.argv.slice(2);
    if (!args[0] || args.length > 2) {
        process.stderr.write('usage: check-portability.mjs ENTRY [TARGET]\n');
        return 2;
    }
    const result = await checkPortability(args[0], { target: args[1] ?? 'portable' });
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return result.status === 'passed' ? 0 : 1;
}

if (resolve(process.argv[1] ?? '') === fileURLToPath(import.meta.url)) {
    process.exitCode = await main();
}
