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
//   tab i      {W/2 - 1.5 tw + i tw, ty, tw, 26s}, tw = narrow ? min(92s, (W - 2m) / 3) : 92s,
//              ty = narrow ? 50s : 16s, in a pill 3s larger on every side
//   info       a 9.5s circle centered 18s after the wordmark's tracked end, at 25.5s
//   fade       {controls.x, controls.y - (narrow ? 44s : 26s), controls.w, to controls bottom}
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
// Behind everything is the native acoustic field (`superconvolver_field.js`),
// animated by the session's clock. The Tracers, Currents and Field tabs
// switch how it is drawn. Its inputs are the native ones: density is the
// Rooms count, motion is Flow and brightness is the live energy. The web
// build declares neither Rooms nor Flow, so the field draws at density 1
// with no motion, as the native editor does in that build; the spectrum feed
// does not yet reach the energy, which stays 0, as native shows with no
// signal. `options.field` replaces any of {density, flow, energy}, which a
// calibration against a native capture uses to match that capture's state.
//
// Text boxes put the baseline where the native editor puts it: the session's
// Canvas2D path draws a text box's baseline at `y + fontSize`, so a box for a
// native baseline `b` starts at `b - fontSize`. Tracked capitals are drawn one
// glyph at a time with the native advances and letter spacing
// (`tracked_text`).
//
// Some bounds have no native value. Each is marked DESIGN below. They are
// choices made for the web host, not measurements of the native editor:
//   - Bypass. The native editor leaves Bypass to the plug-in host's chrome
//     and draws no control for it. The web page is that host, so this view
//     draws a 96s by 32s Bypass pill at the right edge of the slot the
//     native Engine chip occupies, {W - 200s, 12s, 176s, 44s}, centered in
//     its height: {W - 120s, 18s, 96s, 32s}. The slot is empty on the web
//     because Engine does not exist there; the pill is narrower than the
//     slot so a narrow canvas keeps it clear of the wordmark. It is painted
//     as the native `paint_chip` paints a toggle chip.
//   - The info glyph's text box, 3s wide, where native gives only an origin.
//   - Text box widths where the native editor gives only an origin, and the
//     0.3 fontSize allowance below the baseline in every text box height.
//   - The split of a wide slider's label band into a left name half and a
//     right value half.
//
// The native engine readout is not part of this view: Engine is compiled out
// on the web. Pressing the info glyph does not yet open the native info card.

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

import {fieldMarks} from './superconvolver_field.js';

// Native colors: the field ground, the concept tokens, the translucent
// chrome and the slider. `label` is the native bone, `sliderName` the label
// token and `value` the text token.
const COLORS = Object.freeze({
    background: '#07090e',
    label: '#bcc2cc', sliderName: '#787e88', value: '#edeff3', faint: '#474c55',
    track: '#dce4ee1f', trackFill: '#ecEff39e', thumb: '#edeff3', thumbRing: '#050607',
    orb: '#e8f0f8', toggleText: '#787e88', toggleTextOn: '#edeff3',
});
// Chrome fills and strokes: rgba(220, 228, 238) at 8 and 26 of 255, and the
// toggle chip's rgba(237, 239, 243) fills and strokes.
const CHROME_FILL = '#dce4ee08';
const CHROME_STROKE = '#dce4ee1a';
const TAB_ON = '#dce4ee1a';
const CHIP = Object.freeze({onFill: '#edeff31e', offFill: '#edeff30c', onStroke: '#edeff35a',
    offStroke: '#edeff324'});
// The fade under the control strip: rgba(5, 6, 9, 0) to rgba(4, 5, 8, 224).
const FADE = Object.freeze(['#05060900', '#040508e0']);
// The info glyph's ring, rgba(220, 228, 238, 56).
const INFO_RING = '#dce4ee38';

export const MODES = Object.freeze(['Tracers', 'Currents', 'Field']);

// The native `tracked_text` per-glyph advance of an uppercase Inter glyph, in ems.
function advance(glyph) {
    if ('IJ'.includes(glyph)) return 0.30;
    if (glyph === 'L') return 0.52;
    if ('SEFPTZ'.includes(glyph)) return 0.60;
    if ('RBKVAXY'.includes(glyph)) return 0.66;
    if ('CDHNUOQG'.includes(glyph)) return 0.73;
    if ('MW'.includes(glyph)) return 0.88;
    if (glyph === ' ') return 0.40;
    return 0.64;
}

// Where native `tracked_text` ends a string of `size` type with `em` spacing.
function trackedEnd(text, x, size, em) {
    for (const glyph of text.toUpperCase()) x += size * (advance(glyph) + em);
    return x;
}

// A rounded rectangle's path in a box's own coordinates, offset by `inset`.
function roundedRect(width, height, radius, inset = 0) {
    const r = Math.max(0, Math.min(radius, width / 2, height / 2));
    const x0 = inset;
    const y0 = inset;
    const x1 = inset + width;
    const y1 = inset + height;
    return `M${x0 + r} ${y0}H${x1 - r}A${r} ${r} 0 0 1 ${x1} ${y0 + r}V${y1 - r}` +
        `A${r} ${r} 0 0 1 ${x1 - r} ${y1}H${x0 + r}A${r} ${r} 0 0 1 ${x0} ${y1 - r}` +
        `V${y0 + r}A${r} ${r} 0 0 1 ${x0 + r} ${y0}Z`;
}

// A box filled and stroked with a 1 pixel line, as native
// `fill_rounded_rect` then `stroke_rounded_rect` draw it.
function chip(box, radius, fill, stroke) {
    return {viewBox: [0, 0, box.width, box.height],
        ops: [{op: 'path', d: roundedRect(box.width, box.height, radius), fill, stroke,
            strokeWidth: 1}]};
}

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
    if (extra.tracking !== undefined) extra = {...extra, advance};
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

    // Wordmark: tracked capitals at (24s, baseline 30s), 11s type, 0.24 em
    // spacing. Width is DESIGN.
    put('wordmark', text(24 * s, 30 * s, 160 * s, 11 * s, 'left', {uppercase: true, tracking: 0.24}));

    // Info glyph: a 9.5s ring 18s after the wordmark's tracked end, and an
    // "i" at 0.14 em left of its centre on baseline 4s below it. The box is
    // the native hit rectangle, 4s larger than the ring.
    {
        const ix = trackedEnd('SuperConvolver', 24 * s, 11 * s, 0.24) + 18 * s;
        const iy = 25.5 * s;
        const ir = 9.5 * s;
        bounds.info = {x: ix - ir - 4 * s, y: iy - ir - 4 * s, width: 2 * (ir + 4 * s),
            height: 2 * (ir + 4 * s)};
        styles.info = {ring: {x: ix, y: iy, radius: ir}};
        put('info/glyph', text(ix - 11 * s * 0.14, iy + 4 * s, 3 * s, 11 * s));
    }

    // Mode tabs: three equal cells centered at the top, in a pill 3s larger.
    {
        const tw = narrow ? Math.min(92 * s, (W - 2 * m) / 3) : 92 * s;
        const th = 26 * s;
        const ty = narrow ? 50 * s : 16 * s;
        const x0 = W * 0.5 - tw * 1.5;
        const pad = 3 * s;
        bounds.tabs = {x: x0 - pad, y: ty - pad, width: 3 * tw + 2 * pad, height: th + 2 * pad};
        styles.tabs = {radius: 9 * s};
        MODES.forEach((_, index) => {
            const tab = {x: x0 + index * tw, y: ty, width: tw, height: th};
            bounds[`tab/${index}`] = tab;
            styles[`tab/${index}`] = {radius: 7 * s};
            put(`tab/${index}/text`, text(tab.x, tab.y + th * 0.64, tw, 10.5 * s, 'center'));
        });
    }

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
        source.width - 46 * s, 8.5 * s, 'left', {uppercase: true, tracking: 0.2}));

    // The fade under the strip, from the label band's top to the strip's bottom.
    const fadeTop = controls.y - (narrow ? 44 : 26) * s;
    bounds.fade = {x: controls.x, y: fadeTop, width: controls.width,
        height: controls.y + controls.height - fadeTop};

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
            put(`${id}/name`, text(track.x, track.y - 12 * s, half, 9.5 * s, 'left',
                {uppercase: true, tracking: 0.26}));
            put(`${id}/value`, text(track.x + half, track.y - 11 * s, half, 14 * s, 'right'));
        }
    });

    if (bypass) {
        // DESIGN: inside the empty Engine slot. Label centered, baseline at
        // 0.62 of the pill, 12s type, as native `paint_chip`.
        const pill = {x: W - 120 * s, y: 18 * s, width: 96 * s, height: 32 * s};
        bounds.bypass = pill;
        put('bypass/text', text(pill.x, pill.y + pill.height * 0.62, pill.width, 12 * s, 'center'));
    }
    return {bounds, styles, scale: s, narrow};
}

// The view for the parameters the adapter reported. A slider appears only
// when its parameter does, so the strip divides by the live count.
// `options.field` replaces the field's {density, flow, energy}; see above.
export function createSuperconvolverView(parameterInfos,
    {onLoadImpulseResponse = null, field = {}} = {}) {
    const present = new Set(parameterInfos.map(info => info.id));
    const sliders = SLIDERS.filter(slider => present.has(slider.param));
    const hasBypass = present.has(BYPASS.param);
    let current = {styles: {}};
    let mode = 0;
    const radius = key => current.styles[key]?.radius ?? 0;

    const nodes = [
        {id: 'wordmark', kind: 'label', text: 'SuperConvolver'},
        {id: 'info', kind: 'vector', vector: box => {
            const ring = current.styles.info.ring;
            const d = `M${ring.x - box.x - ring.radius} ${ring.y - box.y}` +
                `a${ring.radius} ${ring.radius} 0 1 0 ${2 * ring.radius} 0` +
                `a${ring.radius} ${ring.radius} 0 1 0 ${-2 * ring.radius} 0Z`;
            return {viewBox: [0, 0, box.width, box.height],
                ops: [{op: 'path', d, fill: 'none', stroke: INFO_RING, strokeWidth: 1}]};
        }},
        {id: 'info/glyph', kind: 'label', text: 'i', color: 'sliderName'},
        {id: 'tabs', kind: 'vector',
            vector: box => chip(box, radius('tabs'), CHROME_FILL, CHROME_STROKE)},
        ...MODES.flatMap((name, index) => [
            {id: `tab/${index}`, kind: 'vector', action: `mode-${index}`,
                vector: box => ({viewBox: [0, 0, box.width, box.height], ops: mode === index
                    ? [{op: 'path', d: roundedRect(box.width, box.height, radius(`tab/${index}`)),
                        fill: TAB_ON, stroke: 'none'}] : []})},
            {id: `tab/${index}/text`, kind: 'label', text: name,
                colorFor: () => (mode === index ? 'value' : 'sliderName')},
        ]),
        ...(hasBypass ? [{...BYPASS, kind: 'toggle', vector: (box, state) => {
            const on = state.value(BYPASS.param) >= 0.5;
            return chip(box, box.height / 2, on ? CHIP.onFill : CHIP.offFill,
                on ? CHIP.onStroke : CHIP.offStroke);
        }}] : []),
        {id: 'source', kind: 'button', action: 'load-ir',
            vector: box => chip(box, radius('source'), CHROME_FILL, CHROME_STROKE)},
        {id: 'source/orb', kind: 'shape', color: 'orb'},
        {id: 'source/name', kind: 'label', color: 'value',
            format: state => sourceLabel(state.impulseResponseName)},
        {id: 'source/caption', kind: 'label', text: 'Source', color: 'faint'},
        {id: 'fade', kind: 'vector', vector: box => ({viewBox: [0, 0, box.width, box.height],
            ops: [{op: 'path', d: `M0 0H${box.width}V${box.height}H0Z`, stroke: 'none',
                // A gradient is laid out on the unit square; the transform
                // stretches it over the box.
                fill: {transform: [box.width, 0, 0, box.height, 0, 0],
                    gradient: {kind: 'linear', angle: 180, stops: [
                        {position: 0, color: FADE[0]}, {position: 1, color: FADE[1]}]}}}]})},
        ...sliders.map(slider => ({...slider, kind: 'slider'})),
    ];

    const actions = {
        'load-ir': session => onLoadImpulseResponse?.(session),
    };
    MODES.forEach((_, index) => {
        actions[`mode-${index}`] = () => { mode = index; };
    });

    return {
        background: COLORS.background,
        colors: COLORS,
        nodes,
        animated: true,
        get mode() { return mode; },
        layout(width, height) {
            current = superconvolverLayout(width, height, sliders.map(slider => slider.id),
                {bypass: hasBypass});
            return current.bounds;
        },
        textStyle(key) {
            return current.styles[key] ?? null;
        },
        // The native field's inputs are density from Rooms, motion from Flow
        // and brightness from the live energy. Rooms and Flow are not web
        // parameters, so density is native's floor of 1 and there is no motion.
        backdrop(width, height, state) {
            return fieldMarks({
                w: width, h: height, t: state.time, mode,
                density: field.density ?? 1, flow: field.flow ?? 0, energy: field.energy ?? 0,
            });
        },
        actions,
    };
}
