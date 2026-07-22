#!/usr/bin/env node
import { readFile, writeFile } from 'node:fs/promises';
import process from 'node:process';
import {
    normalizeImport,
    parseAuthoredOverlay,
    parseDesignIR,
    reimportDesign,
    stableStringify,
    summarizeDesignIR,
} from '../src/index.js';

const [command, ...rawArgs] = process.argv.slice(2);

try {
    const args = parseArgs(rawArgs);
    if (command === 'normalize') {
        const input = JSON.parse(await requiredFile(args, 'input'));
        await emit(args.output, normalizeImport(input));
    } else if (command === 'reimport') {
        const previous = parseDesignIR(await requiredFile(args, 'previous'));
        const next = parseDesignIR(await requiredFile(args, 'next'));
        const overlay = parseAuthoredOverlay(await requiredFile(args, 'overlay'));
        const result = reimportDesign(previous, next, overlay);
        await emit(args.output, {
            accepted: result.accepted,
            materialized: result.materialized,
            report: result.report,
            resolvedBindings: result.resolvedBindings,
            tokenLayers: result.tokenLayers,
        });
        if (!result.accepted) process.exitCode = 2;
    } else if (command === 'inspect') {
        const document = parseDesignIR(await requiredFile(args, 'input'));
        await emit(args.output, summarizeDesignIR(document));
    } else {
        usage();
        process.exitCode = command ? 64 : 0;
    }
} catch (error) {
    process.stderr.write(stableStringify({
        error: {
            issues: error.issues ?? undefined,
            message: error.message,
            name: error.name,
        },
        ok: false,
    }));
    process.exitCode = 1;
}

function parseArgs(args) {
    const output = {};
    for (let index = 0; index < args.length; index += 1) {
        const argument = args[index];
        if (!argument.startsWith('--')) throw new TypeError(`unexpected argument '${argument}'`);
        const name = argument.slice(2);
        const value = args[index + 1];
        if (!value || value.startsWith('--')) throw new TypeError(`--${name} requires a value`);
        output[name] = value;
        index += 1;
    }
    return output;
}

async function requiredFile(args, name) {
    if (!args[name]) throw new TypeError(`--${name} is required`);
    return readFile(args[name], 'utf8');
}

async function emit(path, value) {
    const text = stableStringify(value);
    if (!path || path === '-') process.stdout.write(text);
    else await writeFile(path, text, 'utf8');
}

function usage() {
    process.stdout.write(`Usage:
  vellum-design-ir normalize --input source.json [--output designir.json]
  vellum-design-ir inspect --input designir.json [--output summary.json]
  vellum-design-ir reimport --previous a.json --next b.json --overlay authored.json [--output result.json]
`);
}
