#!/usr/bin/env node

import { resolve } from 'node:path';
import process from 'node:process';
import { stableStringify } from '../src/index.js';
import { executeBackendCommand } from './backend-commands.js';
import {
    BACKEND_SCHEMA,
    fail,
    parseArguments,
} from './backend-protocol.js';
import { validateProject } from './backend-filesystem.js';

const [command, ...rawArguments] = process.argv.slice(2);

try {
    const args = parseArguments(rawArguments);
    if (!args.project) fail('invalid_arguments', '--project is required');
    if (!args.json) fail('invalid_arguments', '--json is required by the backend protocol');
    const project = resolve(args.project);
    await validateProject(project);
    const payload = await executeBackendCommand(command, project, args);
    process.stdout.write(stableStringify(payload, { space: 0 }));
} catch (error) {
    const status = error.status ?? 'backend_error';
    const diagnostics = Array.isArray(error.diagnostics) ? error.diagnostics : [];
    process.stdout.write(stableStringify({
        data: {},
        diagnostics,
        message: error.message,
        ok: false,
        schema: BACKEND_SCHEMA,
        status,
    }, { space: 0 }));
    process.exitCode = Number.isInteger(error.exitCode) ? error.exitCode : 1;
}
