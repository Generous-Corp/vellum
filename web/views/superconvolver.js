// The Superconvolver editor as an editor-session view.
//
// The view draws what the native editor draws (Pulp `examples/super-convolver/
// super_convolver_ui.hpp`, `paint()`), at the native editor's geometry
// (`layout()` and `paint_controls()`), computed from the canvas size alone:
//
//   s = max(0.5, H / 560), m = 20s, narrow = W < 680s
//   top = narrow ? 132s : 64s, avail = H - top - m
//   controls   {m, top + 0.68 avail + 24s, W - 2m, avail - 0.68 avail - 24s}
//              (below the native IR and spectrum slots, 0.42 and 0.26 of
//              avail with two 12s gaps; the native editor lays those slots
//              out but does not paint them, and neither does this view)
//   slider i   cell {controls.x + i cw, controls.y, cw, controls.h}, cw = controls.w / n
//              track {cell.x + 14s, cell.y + 0.54 cell.h, cw - 28s, 4s}
//   source     narrow ? {m, 92s, W - 2m, 36s} : {24s, 44s, 196s, 38s}
//
// where n is the number of sliders whose parameter the plug-in declared.
// The web build of the plug-in declares Mix, Size, Gain and Bypass only;
// Engine, Rooms and Flow are compiled out, so this view never names them and
// never reads them.
//
// The Source chip shows the loaded impulse response's name, cleaned the way
// the native `clean_source_name` cleans a path, or "Synthetic room" before
// one is loaded. Pressing it asks the page for a file, as the native chip's
// `open_ir_chooser` does in a browser build. The impulse-response and
// spectrum feeds reach the session; only the name is drawn.
//
// Text boxes put the baseline where the native editor puts it: the session's
// Canvas2D path draws a text box's baseline at `y + fontSize`, so a box for a
// native baseline `b` starts at `b - fontSize`. Tracked capitals are drawn as
// capitals without the native letter spacing.
//
// Some bounds have no native value. Each is marked DESIGN below. They are
// choices made for the web host, not measurements of the native editor:
//   - Bypass. The native editor leaves Bypass to the plug-in host's chrome
//     and draws no control for it. The web page is that host, so this view
//     draws a 96s by 32s Bypass pill at the right edge of the slot the
//     native Engine chip occupies, {W - 200s, 12s, 176s, 44s}, centered in
//     its height: {W - 120s, 18s, 96s, 32s}. The slot is empty on the web
//     because Engine does not exist there; the pill is narrower than the
//     slot so a narrow canvas keeps it clear of the wordmark.
//   - Text box widths where the native editor gives only an origin, and the
//     0.3 fontSize allowance below the baseline in every text box height.
//   - The split of a wide slider's label band into a left name half and a
//     right value half.
//
// The native editor's acoustic field backdrop, mode tabs, info glyph,
// control-strip fade and engine readout are not part of this view.

// Parameter ids, from Pulp `examples/super-convolver/super_convolver_ui_host.hpp`.
export const PARAMETERS = Object.freeze({mix: 1, size: 2, gain: 3, bypass: 4});

// The native slider table (`all_sliders()`), restricted to the web set.
export const SLIDERS = Object.freeze([
    Object.freeze({id: 'mix', param: PARAMETERS.mix, label: 'Mix', decimals: 0, unit: '%'}),
    Object.freeze({id: 'size', param: PARAMETERS.size, label: 'Size', decimals: 2, unit: 's'}),
    Object.freeze({id: 'gain', param: PARAMETERS.gain, label: 'Gain', decimals: 1, unit: 'dB'}),
]);

export const BYPASS = Object.freeze({id: 'bypass', param: PARAMETERS.bypass, label: 'Bypass'});

export const SYNTHETIC_SOURCE = 'Synthetic room';

// Native colors: the field ground, the concept tokens and the chip.
const COLORS = Object.freeze({
    background: '#07090e',
    label: '#bcc2cc', sliderName: '#787e88', value: '#edeff3', faint: '#474c55',
    track: '#21242a', trackFill: '#959799', thumb: '#edeff3',
    button: '#0e1015', buttonBorder: '#1b1e24', orb: '#e8f0f8',
    toggleOff: '#14161c', toggleOn: '#f6b847', toggleText: '#edeff3', toggleTextOn: '#07090e',
});

// Text allowance below the baseline. DESIGN.
const DESCENT = 0.3;

// Trailing file-name tokens that describe the recording, not the room: the
// native `is_engineering_token`.
const ENGINEERING_WORDS = new Set(['ir', 'stereo', 'mono', 'wav', 'aiff', 'aif', 'flac', 'ogg',
    'mp3', 'wave']);
const ENGINEERING_NUMBERS = new Set([8, 16, 24, 32, 8000, 11025, 16000, 22050, 32000, 44100,
    48000, 88200, 96000, 176400, 192000]);

function isEngineeringToken(token) {
    const lower = token.toLowerCase();
    if (ENGINEERING_WORDS.has(lower)) return true;
    const match = /^([0-9]+)(.*)$/.exec(lower);
    if (!match) return false;
    if (['k', 'khz', 'bit', 'b', 'hz'].includes(match[2])) return true;
    return match[2] === '' && ENGINEERING_NUMBERS.has(Number(match[1]));
}

// The display name of an impulse response from its file name: the native
// `clean_source_name`.
export function cleanSourceName(path) {
    let stem = String(path).split(/[/\\]/).pop();
    const dot = stem.lastIndexOf('.');
    if (dot > 0) stem = stem.slice(0, dot);
    const tokens = stem.replace(/[_.-]/g, ' ').split(' ').filter(Boolean);
    while (tokens.length && isEngineeringToken(tokens[tokens.length - 1])) tokens.pop();
    const name = tokens.map(word => /^[a-z]+$/.test(word)
        ? word[0].toUpperCase() + word.slice(1) : word).join(' ');
    return name || stem;
}

export function sourceLabel(impulseResponseName) {
    return impulseResponseName ? cleanSourceName(impulseResponseName) : SYNTHETIC_SOURCE;
}

function text(x, baseline, width, fontSize, align = 'left', extra = {}) {
    return {box: {x, y: baseline - fontSize, width, height: fontSize * (1 + DESCENT)},
        style: {fontSize, align, ...extra}};
}

// Every named bound and style at a canvas of `width` by `height` CSS pixels,
// with one slider per entry of `sliderIds`, in order.
export function superconvolverLayout(width, height, sliderIds, {bypass = true} = {}) {
    const W = width, H = height;
    const s = Math.max(0.5, H / 560);
    const m = 20 * s;
    const narrow = W < 680 * s;
    const top = narrow ? 132 * s : 64 * s;
    const avail = H - top - m;

    const bounds = {};
    const styles = {};
    const put = (key, entry) => {
        bounds[key] = entry.box;
        styles[key] = entry.style;
    };

    const irH = avail * 0.42;
    const specH = avail * 0.26;
    const ctlH = avail - irH - specH - 2 * (12 * s);
    const controls = {x: m, y: top + irH + 12 * s + specH + 12 * s, width: W - 2 * m, height: ctlH};
    bounds.controls = controls;

    // Wordmark: tracked capitals at (24s, baseline 30s), 11s type. Width is DESIGN.
    put('wordmark', text(24 * s, 30 * s, 160 * s, 11 * s, 'left', {uppercase: true}));

    // Source chip: radius 10s; a 7s orb centered 18s in; the name at 34s in,
    // baseline 0.46 of the chip, 12s type, within the native name_max of
    // width - 46s; the caption at 34s in, baseline 0.82, 8.5s tracked capitals.
    const source = narrow ? {x: m, y: 92 * s, width: W - 2 * m, height: 36 * s}
        : {x: 24 * s, y: 44 * s, width: 196 * s, height: 38 * s};
    bounds.source = source;
    styles.source = {radius: 10 * s};
    bounds['source/orb'] = {x: source.x + 11 * s, y: source.y + source.height / 2 - 7 * s,
        width: 14 * s, height: 14 * s};
    styles['source/orb'] = {radius: 7 * s};
    put('source/name', text(source.x + 34 * s, source.y + source.height * 0.46,
        source.width - 46 * s, 12 * s));
    put('source/caption', text(source.x + 34 * s, source.y + source.height * 0.82,
        source.width - 46 * s, 8.5 * s, 'left', {uppercase: true}));

    const cw = controls.width / sliderIds.length;
    sliderIds.forEach((id, index) => {
        const cell = {x: controls.x + index * cw, y: controls.y, width: cw, height: controls.height};
        const hpad = 14 * s;
        const track = {x: cell.x + hpad, y: cell.y + cell.height * 0.54, width: cw - 2 * hpad, height: 4 * s};
        bounds[id] = cell;
        bounds[`${id}/track`] = track;
        if (narrow) {
            // Label over value, both centered; the band is the native fade
            // band above the track (44s).
            bounds[`${id}/label`] = {x: track.x, y: track.y - 44 * s, width: track.width, height: 44 * s};
            put(`${id}/name`, text(track.x, track.y - 30 * s, track.width, 9.5 * s, 'center'));
            put(`${id}/value`, text(track.x, track.y - 13 * s, track.width, 13 * s, 'center'));
        } else {
            // Name in tracked capitals left at baseline track.y - 12s (9.5s
            // type), value right at track.y - 11s (14s type), inside the
            // native 26s label band.
            bounds[`${id}/label`] = {x: track.x, y: track.y - 26 * s, width: track.width, height: 26 * s};
            const half = track.width / 2;
            put(`${id}/name`, text(track.x, track.y - 12 * s, half, 9.5 * s, 'left', {uppercase: true}));
            put(`${id}/value`, text(track.x + half, track.y - 11 * s, half, 14 * s, 'right'));
        }
    });

    if (bypass) {
        // DESIGN: inside the empty Engine slot. Label centered, baseline at
        // 0.66 of the pill, 12s type.
        const chip = {x: W - 120 * s, y: 18 * s, width: 96 * s, height: 32 * s};
        bounds.bypass = chip;
        put('bypass/text', text(chip.x, chip.y + chip.height * 0.66, chip.width, 12 * s, 'center'));
    }
    return {bounds, styles, scale: s, narrow};
}

// The view for the parameters the adapter reported. A slider appears only
// when its parameter does, so the strip divides by the live count.
export function createSuperconvolverView(parameterInfos, {onLoadImpulseResponse = null} = {}) {
    const present = new Set(parameterInfos.map(info => info.id));
    const sliders = SLIDERS.filter(slider => present.has(slider.param));
    const hasBypass = present.has(BYPASS.param);
    let current = {styles: {}};

    const nodes = [
        {id: 'wordmark', kind: 'label', text: 'SuperConvolver'},
        {id: 'source', kind: 'button', action: 'load-ir'},
        {id: 'source/orb', kind: 'shape', color: 'orb'},
        {id: 'source/name', kind: 'label', color: 'value',
            format: state => sourceLabel(state.impulseResponseName)},
        {id: 'source/caption', kind: 'label', text: 'Source', color: 'faint'},
        ...sliders.map(slider => ({...slider, kind: 'slider'})),
        ...(hasBypass ? [{...BYPASS, kind: 'toggle'}] : []),
    ];

    return {
        background: COLORS.background,
        colors: COLORS,
        nodes,
        layout(width, height) {
            current = superconvolverLayout(width, height, sliders.map(slider => slider.id),
                {bypass: hasBypass});
            return current.bounds;
        },
        textStyle(key) {
            return current.styles[key] ?? null;
        },
        actions: {
            'load-ir': session => onLoadImpulseResponse?.(session),
        },
    };
}
