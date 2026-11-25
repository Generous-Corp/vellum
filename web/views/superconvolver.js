// The Superconvolver editor as an editor-session view.
//
// The geometry is the native editor's (Pulp `examples/super-convolver/
// super_convolver_ui.hpp`, `layout()` and `paint_controls()`), computed from
// the canvas size alone:
//
//   s = max(0.5, H / 560), m = 20s, narrow = W < 680s
//   top = narrow ? 132s : 64s, avail = H - top - m
//   IR panel   {m, top, W - 2m, 0.42 avail}
//   spectrum   {m, IR bottom + 12s, W - 2m, 0.26 avail}
//   controls   {m, spectrum bottom + 12s, W - 2m, the rest of avail}
//   slider i   cell {controls.x + i cw, controls.y, cw, controls.h}, cw = controls.w / n
//              track {cell.x + 14s, cell.y + 0.54 cell.h, cw - 28s, 4s}
//
// where n is the number of sliders whose parameter the plug-in declared.
// The web build of the plug-in declares Mix, Size, Gain and Bypass only;
// Engine, Rooms and Flow are compiled out, so this view never names them and
// never reads them.
//
// Text boxes put the baseline where the native editor puts it: the session's
// Canvas2D path draws a text box's baseline at `y + fontSize`, so a box for a
// native baseline `b` starts at `b - fontSize`.
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
// The native editor's acoustic field backdrop, mode tabs, info glyph and
// engine readout are not part of this view.

// Parameter ids, from Pulp `examples/super-convolver/super_convolver_ui_host.hpp`.
export const PARAMETERS = Object.freeze({mix: 1, size: 2, gain: 3, bypass: 4});

// The native slider table (`all_sliders()`), restricted to the web set.
export const SLIDERS = Object.freeze([
    Object.freeze({id: 'mix', param: PARAMETERS.mix, label: 'Mix', decimals: 0, unit: '%'}),
    Object.freeze({id: 'size', param: PARAMETERS.size, label: 'Size', decimals: 2, unit: 's'}),
    Object.freeze({id: 'gain', param: PARAMETERS.gain, label: 'Gain', decimals: 1, unit: 'dB'}),
]);

export const BYPASS = Object.freeze({id: 'bypass', param: PARAMETERS.bypass, label: 'Bypass'});

// Native palette: the field ground and the Ink & Signal fallbacks.
const COLORS = Object.freeze({
    background: '#07090e',
    plot: '#1e2030', plotFill: '#16dac2',
    label: '#bcc2cc', labelDim: '#969ebc', value: '#edeff3',
    track: '#262a33', trackFill: '#a4a8ad', thumb: '#edeff3',
    button: '#2e3146', buttonText: '#16dac2',
    toggleOff: '#14161c', toggleOn: '#f6b847', toggleText: '#edeff3', toggleTextOn: '#07090e',
});

// Text allowance below the baseline. DESIGN.
const DESCENT = 0.3;

function text(x, baseline, width, fontSize, align = 'left') {
    return {box: {x, y: baseline - fontSize, width, height: fontSize * (1 + DESCENT)},
        style: {fontSize, align}};
}

// Every named bound and text style at a canvas of `width` by `height` CSS
// pixels, with one slider per entry of `sliderIds`, in order.
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
    const ir = {x: m, y: top, width: W - 2 * m, height: irH};
    const spectrum = {x: m, y: ir.y + ir.height + 12 * s, width: W - 2 * m, height: specH};
    const controls = {x: m, y: spectrum.y + spectrum.height + 12 * s, width: W - 2 * m, height: ctlH};
    bounds.ir = ir;
    bounds.spectrum = spectrum;
    bounds.controls = controls;

    // Wordmark at (24s, baseline 30s), 11s type. Width is DESIGN.
    put('wordmark', text(24 * s, 30 * s, 160 * s, 11 * s));

    // The Source chip is the loader. Its text sits 14s in, baseline at 0.66
    // of the chip, 12s type. Text width is the rest of the chip (DESIGN).
    const load = narrow ? {x: m, y: 92 * s, width: W - 2 * m, height: 36 * s}
        : {x: 24 * s, y: 44 * s, width: 196 * s, height: 38 * s};
    bounds.loadIr = load;
    put('loadIr/text', text(load.x + 14 * s, load.y + load.height * 0.66,
        load.width - 14 * s, 12 * s));

    // IR panel header: title at 10s in, source on the right 55% (the native
    // clip rect), both at baseline 16s, 11s type. Title width is DESIGN.
    put('irTitle', text(ir.x + 10 * s, ir.y + 16 * s, ir.width * 0.45 - 10 * s, 11 * s));
    put('irSource', text(ir.x + ir.width * 0.45, ir.y + 16 * s, ir.width * 0.55 - 10 * s, 11 * s));

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
            // Name left at baseline track.y - 12s (9.5s type), value right at
            // track.y - 11s (14s type), inside the native 26s label band.
            bounds[`${id}/label`] = {x: track.x, y: track.y - 26 * s, width: track.width, height: 26 * s};
            const half = track.width / 2;
            put(`${id}/name`, text(track.x, track.y - 12 * s, half, 9.5 * s));
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
        {id: 'ir', kind: 'plot', feed: 'ir'},
        {id: 'spectrum', kind: 'plot', feed: 'spectrum'},
        {id: 'irTitle', kind: 'label', text: 'impulse response', color: 'labelDim'},
        {id: 'irSource', kind: 'label', color: 'labelDim',
            format: state => `source: ${state.impulseResponseName || 'built-in synthetic'}`},
        {id: 'wordmark', kind: 'label', text: 'SuperConvolver'},
        {id: 'loadIr', kind: 'button', text: 'Load IR', action: 'load-ir'},
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
