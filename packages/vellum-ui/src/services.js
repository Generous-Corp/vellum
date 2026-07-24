const protocol = 'vellum.services.v1';
const supported = Object.freeze({
    commands: 'v1',
    files: 'user-selected-text-v1',
    clipboard: 'text-v1',
    open_url: 'external-v1',
    persistence: 'state-v1',
});
const operations = Object.freeze({
    commands: new Set(['execute']),
    files: new Set(['selectText']),
    clipboard: new Set(['readText', 'writeText']),
    open_url: new Set(['openExternal']),
    persistence: new Set(['loadState', 'saveState']),
});

function error(code, message) {
    const value = new Error(message);
    value.code = code;
    return value;
}

function capabilityError(service, declaration) {
    const code = declaration === 'denied' ? 'capability-denied' : 'unsupported';
    return error(code, `${service} capability is ${declaration ?? 'undeclared'}`);
}

function validateResponse(response, id) {
    if (!response || typeof response !== 'object' ||
        response.protocol !== protocol || response.kind !== 'response' ||
        response.id !== id || typeof response.ok !== 'boolean') {
        throw error('service-failed', 'service provider returned an invalid envelope');
    }
    const expected = response.ok
        ? ['id', 'kind', 'ok', 'protocol', 'value']
        : ['error', 'id', 'kind', 'ok', 'protocol'];
    if (Object.keys(response).sort().join(',') !== expected.sort().join(',')) {
        throw error('service-failed', 'service provider returned unknown fields');
    }
    if (response.ok) return response.value;
    const detail = response.error;
    if (!detail || typeof detail.code !== 'string' || typeof detail.message !== 'string') {
        throw error('service-failed', 'service provider returned an invalid error');
    }
    throw error(detail.code, detail.message);
}

export function createServices(provider, capabilities = {}) {
    if (typeof provider !== 'function') {
        throw new TypeError('createServices requires a request provider');
    }
    let sequence = 0;
    async function request(service, operation, args = {}) {
        const declaration = capabilities[service];
        if (declaration !== supported[service]) {
            throw capabilityError(service, declaration);
        }
        if (!operations[service]?.has(operation)) {
            throw error('unsupported', `unsupported ${service} operation: ${operation}`);
        }
        const id = `request-${++sequence}`;
        const envelope = {
            protocol, kind: 'request', id, service, operation, arguments: args,
        };
        let response;
        try {
            response = await provider(envelope);
        } catch {
            throw error('service-failed', 'service provider failed');
        }
        return validateResponse(response, id);
    }
    return Object.freeze({
        commands: Object.freeze({
            execute: (command, arguments_ = {}) =>
                request('commands', 'execute', { command, arguments: arguments_ }),
        }),
        files: Object.freeze({
            selectText: (options = {}) => request('files', 'selectText', options),
        }),
        clipboard: Object.freeze({
            readText: () => request('clipboard', 'readText'),
            writeText: (text) => request('clipboard', 'writeText', { text }),
        }),
        openUrl: (url) => request('open_url', 'openExternal', { url }),
        persistence: Object.freeze({
            loadState: () => request('persistence', 'loadState'),
            saveState: (state) => request('persistence', 'saveState', { state }),
        }),
    });
}

export const serviceCapabilities = supported;

let installedHost = null;
const commandDefinitions = new Map();
let singletonSequence = 0;

function validateCommandDefinitions(definitions) {
    if (!Array.isArray(definitions) || definitions.length === 0) {
        throw new TypeError('services.commands.define requires a non-empty array');
    }
    for (const definition of definitions) {
        if (!definition || typeof definition !== 'object' || Array.isArray(definition) ||
            typeof definition.id !== 'string' || definition.id.length === 0 ||
            typeof definition.title !== 'string' || definition.title.length === 0 ||
            (definition.shortcut !== undefined && typeof definition.shortcut !== 'string')) {
            throw new TypeError('services.commands.define received an invalid command');
        }
        if (commandDefinitions.has(definition.id)) {
            throw new Error(`command is already defined: ${definition.id}`);
        }
    }
}

/**
 * Installs the platform service boundary used by the singleton `services`
 * facade. This is a host API, not an application capability grant.
 */
export function installServiceHost(provider, capabilities = {}) {
    if (typeof provider !== 'function') {
        throw new TypeError('installServiceHost requires a request provider');
    }
    const prior = installedHost;
    installedHost = { provider, capabilities: { ...capabilities } };
    return () => {
        if (installedHost?.provider === provider) installedHost = prior;
    };
}

async function invokeSingleton(service, operation, arguments_ = {}) {
    const host = installedHost ?? globalThis.__vellumServiceHost;
    const provider = typeof host?.request === 'function'
        ? (request) => host.request(request)
        : typeof host?.provider === 'function' ? host.provider : null;
    const capabilities = host?.capabilities ?? {};
    if (provider === null) throw capabilityError(service, 'denied');
    if (capabilities[service] !== supported[service]) {
        throw capabilityError(service, capabilities[service]);
    }
    if (!operations[service]?.has(operation)) {
        throw error('unsupported', `unsupported ${service} operation: ${operation}`);
    }
    const id = `request-${++singletonSequence}`;
    const envelope = {
        protocol, kind: 'request', id, service, operation, arguments: arguments_,
    };
    let response;
    try {
        response = await provider(envelope);
    } catch {
        throw error('service-failed', 'service provider failed');
    }
    return validateResponse(response, id);
}

export const services = Object.freeze({
    commands: Object.freeze({
        define(definitions) {
            validateCommandDefinitions(definitions);
            for (const definition of definitions) {
                commandDefinitions.set(definition.id, Object.freeze({ ...definition }));
            }
        },
        execute: (command, arguments_ = {}) =>
            invokeSingleton('commands', 'execute', { command, arguments: arguments_ }),
        has: (command) => commandDefinitions.has(command),
        definitions: () => Object.freeze([...commandDefinitions.values()]),
    }),
    files: Object.freeze({
        openText: (options = {}) => invokeSingleton('files', 'selectText', options),
        selectText: (options = {}) => invokeSingleton('files', 'selectText', options),
    }),
    clipboard: Object.freeze({
        readText: () => invokeSingleton('clipboard', 'readText'),
        writeText: (text) => invokeSingleton('clipboard', 'writeText', { text }),
    }),
    urls: Object.freeze({
        openExternal: (url) => invokeSingleton('open_url', 'openExternal', { url }),
    }),
    persistence: Object.freeze({
        loadState: () => invokeSingleton('persistence', 'loadState'),
        saveState: (state) => invokeSingleton('persistence', 'saveState', { state }),
    }),
});
