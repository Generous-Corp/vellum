import { canonicalize } from './stable-json.js';

export const AUDIO_CONTROL_MAP_SCHEMA = 'https://vellum.dev/schemas/audio-control-map/v1';
export const AUDIO_CONTROL_MAP_VERSION = 1;

/**
 * Validate the renderer-neutral control/audio contract emitted by plugin design
 * importers. The map deliberately carries no Pulp or Beadsy implementation
 * names: Vellum can materialize the regions while a product owns behavior.
 */
export function validateAudioControlMap(value) {
    if (!isObject(value)) throw new TypeError('audio control map must be an object');
    if (value.$schema !== AUDIO_CONTROL_MAP_SCHEMA) throw new TypeError('unsupported audio control map schema');
    if (value.schemaVersion !== AUDIO_CONTROL_MAP_VERSION) throw new TypeError('unsupported audio control map version');
    if (!Array.isArray(value.controls)) throw new TypeError('audio control map controls must be an array');
    if (!Array.isArray(value.audioInputs)) throw new TypeError('audio control map audioInputs must be an array');
    const ids = new Set();
    const controls = value.controls.map((control, index) => {
        if (!isObject(control)) throw new TypeError(`controls[${index}] must be an object`);
        string(control.id, `controls[${index}].id`);
        if (ids.has(control.id)) throw new TypeError(`duplicate control id '${control.id}'`);
        ids.add(control.id);
        string(control.role, `controls[${index}].role`);
        if (!['knob', 'button', 'toggle', 'meter', 'picker'].includes(control.role)) {
            throw new TypeError(`controls[${index}].role is unsupported`);
        }
        if (control.parameterId !== undefined) string(control.parameterId, `controls[${index}].parameterId`);
        if (control.range !== undefined) validateRange(control.range, `controls[${index}].range`);
        if (control.lfo !== undefined) validateLfo(control.lfo, `controls[${index}].lfo`);
        return canonicalize(control);
    });
    const audioInputs = value.audioInputs.map((input, index) => {
        if (!isObject(input)) throw new TypeError(`audioInputs[${index}] must be an object`);
        string(input.id, `audioInputs[${index}].id`);
        string(input.label, `audioInputs[${index}].label`);
        if (!['main', 'sidechain', 'physical-input', 'project-track', 'project-bus'].includes(input.kind)) {
            throw new TypeError(`audioInputs[${index}].kind is unsupported`);
        }
        if (input.channels !== undefined && (!Number.isInteger(input.channels) || input.channels < 1)) {
            throw new TypeError(`audioInputs[${index}].channels must be a positive integer`);
        }
        return canonicalize(input);
    });
    return canonicalize({
        $schema: AUDIO_CONTROL_MAP_SCHEMA,
        schemaVersion: AUDIO_CONTROL_MAP_VERSION,
        controls,
        audioInputs,
    });
}

function validateRange(value, path) {
    if (!isObject(value)) throw new TypeError(`${path} must be an object`);
    for (const key of ['min', 'max', 'step']) {
        if (typeof value[key] !== 'number' || !Number.isFinite(value[key])) throw new TypeError(`${path}.${key} must be finite`);
    }
    if (value.max < value.min || value.step <= 0) throw new TypeError(`${path} has invalid bounds`);
}
function validateLfo(value, path) {
    if (!isObject(value)) throw new TypeError(`${path} must be an object`);
    for (const key of ['rateMin', 'rateMax', 'depthMin', 'depthMax']) {
        if (typeof value[key] !== 'number' || !Number.isFinite(value[key])) throw new TypeError(`${path}.${key} must be finite`);
    }
    if (value.rateMin < 0 || value.rateMax <= value.rateMin || value.depthMin < 0 || value.depthMax > 1 || value.depthMax < value.depthMin) {
        throw new TypeError(`${path} has invalid bounds`);
    }
}
function string(value, path) { if (typeof value !== 'string' || value.length === 0) throw new TypeError(`${path} must be a non-empty string`); }
function isObject(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
