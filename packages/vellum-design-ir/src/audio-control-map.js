import { canonicalize } from './stable-json.js';

export const AUDIO_CONTROL_MAP_SCHEMA = 'https://vellum.dev/schemas/audio-control-map/v1';
export const AUDIO_CONTROL_MAP_VERSION = 1;

const MAP_KEYS = ['$schema', 'schemaVersion', 'controls', 'audioInputs'];
const CONTROL_KEYS = ['id', 'role', 'parameterId', 'range', 'lfo'];
const RANGE_KEYS = ['min', 'max', 'step'];
const LFO_KEYS = ['rateMin', 'rateMax', 'depthMin', 'depthMax'];
const INPUT_KEYS = ['id', 'label', 'kind', 'channels'];
const ROLES = ['knob', 'button', 'toggle', 'meter', 'picker'];
const INPUT_KINDS = ['main', 'sidechain', 'physical-input', 'project-track', 'project-bus'];

/**
 * Validate the renderer-neutral control/audio contract that plugin design
 * importers emit, and return a copy built only from the validated fields. The
 * map carries no product implementation names: Vellum can materialize the
 * regions while a product owns their behavior.
 *
 * Validation is as strict as schema/audio-control-map-v1.schema.json: every
 * object must be a plain object carrying only the keys the schema names, and
 * control ids and audio-input ids must each be unique within their list. It
 * also enforces orderings the schema cannot express (min <= max, rateMin <
 * rateMax, depthMin <= depthMax). Any violation throws a TypeError.
 */
export function validateAudioControlMap(value) {
    plainObject(value, 'audio control map', MAP_KEYS);
    if (value.$schema !== AUDIO_CONTROL_MAP_SCHEMA) throw new TypeError('unsupported audio control map schema');
    if (value.schemaVersion !== AUDIO_CONTROL_MAP_VERSION) throw new TypeError('unsupported audio control map version');
    denseArray(value.controls, 'audio control map controls');
    denseArray(value.audioInputs, 'audio control map audioInputs');
    const controlIds = new Set();
    const controls = value.controls.map((control, index) => {
        const path = `controls[${index}]`;
        plainObject(control, path, CONTROL_KEYS);
        string(control.id, `${path}.id`);
        if (controlIds.has(control.id)) throw new TypeError(`duplicate control id '${control.id}'`);
        controlIds.add(control.id);
        if (!ROLES.includes(control.role)) throw new TypeError(`${path}.role is unsupported`);
        const out = { id: control.id, role: control.role };
        if (control.parameterId !== undefined) {
            string(control.parameterId, `${path}.parameterId`);
            out.parameterId = control.parameterId;
        }
        if (control.range !== undefined) out.range = validateRange(control.range, `${path}.range`);
        if (control.lfo !== undefined) out.lfo = validateLfo(control.lfo, `${path}.lfo`);
        return out;
    });
    const inputIds = new Set();
    const audioInputs = value.audioInputs.map((input, index) => {
        const path = `audioInputs[${index}]`;
        plainObject(input, path, INPUT_KEYS);
        string(input.id, `${path}.id`);
        if (inputIds.has(input.id)) throw new TypeError(`duplicate audio input id '${input.id}'`);
        inputIds.add(input.id);
        string(input.label, `${path}.label`);
        if (!INPUT_KINDS.includes(input.kind)) throw new TypeError(`${path}.kind is unsupported`);
        const out = { id: input.id, label: input.label, kind: input.kind };
        if (input.channels !== undefined) {
            if (!Number.isInteger(input.channels) || input.channels < 1) {
                throw new TypeError(`${path}.channels must be a positive integer`);
            }
            out.channels = input.channels;
        }
        return out;
    });
    return canonicalize({ $schema: AUDIO_CONTROL_MAP_SCHEMA, schemaVersion: AUDIO_CONTROL_MAP_VERSION, controls, audioInputs });
}

function validateRange(value, path) {
    plainObject(value, path, RANGE_KEYS);
    for (const key of RANGE_KEYS) finite(value[key], `${path}.${key}`);
    if (value.max < value.min || value.step <= 0) throw new TypeError(`${path} has invalid bounds`);
    return { min: value.min, max: value.max, step: value.step };
}

function validateLfo(value, path) {
    plainObject(value, path, LFO_KEYS);
    for (const key of LFO_KEYS) finite(value[key], `${path}.${key}`);
    if (value.rateMin < 0 || value.rateMax <= value.rateMin || value.depthMin < 0 || value.depthMax > 1 || value.depthMax < value.depthMin) {
        throw new TypeError(`${path} has invalid bounds`);
    }
    return { rateMin: value.rateMin, rateMax: value.rateMax, depthMin: value.depthMin, depthMax: value.depthMax };
}

function plainObject(value, path, allowed) {
    if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new TypeError(`${path} must be an object`);
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) throw new TypeError(`${path} must be a plain object`);
    for (const key of Reflect.ownKeys(value)) {
        if (typeof key !== 'string' || !allowed.includes(key)) throw new TypeError(`${path} has unknown field '${String(key)}'`);
    }
}

function denseArray(value, path) {
    if (!Array.isArray(value)) throw new TypeError(`${path} must be an array`);
    for (let index = 0; index < value.length; index += 1) {
        if (!Object.hasOwn(value, index)) throw new TypeError(`${path}[${index}] is missing`);
    }
}

function finite(value, path) {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError(`${path} must be finite`);
}

function string(value, path) {
    if (typeof value !== 'string' || value.length === 0) throw new TypeError(`${path} must be a non-empty string`);
}
