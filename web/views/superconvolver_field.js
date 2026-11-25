// The Superconvolver acoustic field, as backdrop marks for the editor session.
//
// A port of Pulp `examples/super-convolver/field_renderer.hpp`
// (`draw_acoustic_field`). The field is stateless: every emitter is seeded by
// a per-index hash and advected through one coherent flow field evaluated at
// time `t`, so the same arguments give the same frame and animating it is
// only advancing `t`.
//
// The native code computes in single precision, and the hash multiplies the
// error of its sine by about 43758, so this port rounds every operation to
// float with `Math.fround`. Where the native build contracts a multiply and
// an add into one fused operation (`-ffp-contract=on`), this port does the
// same: the product of two floats is exact in a double, so `f(a * b + c)` is
// the fused result. The sine is the correctly rounded single-precision sine;
// a platform `sinf` that is not correctly rounded seeds some emitters one
// float step away.
//
// The marks are a flat Float32Array of records, drawn in order with additive
// ("lighter") blending, as the native field draws with BlendMode::lighter:
//   DOT   [0, x, y, radius, red, green, blue, alpha, 0]
//   GLOW  [1, x, y, radius, red, green, blue, alpha, middleAlpha]
//         a radial gradient from alpha at the centre to middleAlpha at 0.38
//         of the radius to transparent at the edge
//   LINE  [2, count, width, 0, red, green, blue, alpha, 0]
//         followed by `count` x, y pairs, stroked as one open polyline
// Colours are 0 to 1. Alphas are whole steps of 1/255, as native `rgba8`.

export const MARK_DOT = 0;
export const MARK_GLOW = 1;
export const MARK_LINE = 2;
export const MARK_HEADER = 9;

// Tracers, Currents, Field: the native `viz_mode_` values.
export const FIELD_MODES = Object.freeze(['Tracers', 'Currents', 'Field']);

const f = Math.fround;
// a * b + c with one rounding, as a contracted multiply-add.
const fma = (a, b, c) => f(a * b + c);
const sinf = x => f(Math.sin(f(x)));
const cosf = x => f(Math.cos(f(x)));
// static_cast<std::uint8_t>(min(255, value)) on a non-negative value.
const byte = value => Math.trunc(Math.min(255, value));

// The native `field_hash`.
export function fieldHash(k) {
    const x = f(sinf(fma(k, f(127.1), f(0.7))) * f(43758.5453));
    return f(x - Math.floor(x));
}

const VORTICES = [
    [0.5, 0.4, 0.13, 0.11, 0, 1, 0.15],
    [0.62, 0.5, 0.09, 0.16, 2, 0.5, -0.12],
    [0.32, 0.58, 0.18, 0.08, 4, 3, 0.10],
].map(row => row.map(f));
const K = Object.fromEntries(Object.entries({
    r2: 0.07, a: 0.32, b: 0.18, c: 1.7, d: 0.3, e: 1.1, g: 0.22, h: 1.6, i: 0.26, j: 1.3, l: 0.2,
}).map(([key, value]) => [key, f(value)]));

// The native `field_flow_at`: the coherent flow at unit position (x, y).
export function fieldFlowAt(x, y, t) {
    const tf = f(t);
    let vx = 0;
    let vy = 0;
    for (const [ax, ay, wx, wy, px, py, s] of VORTICES) {
        const ccx = f(ax * sinf(fma(tf, wx, px)));
        const ccy = f(ay * cosf(fma(tf, wy, py)));
        const dx = f(x - ccx);
        const dy = f(y - ccy);
        const r2 = f(fma(dx, dx, f(dy * dy)) + K.r2);
        vx = f(vx + f(f(s * -dy) / r2));
        vy = f(vy + f(f(s * dx) / r2));
    }
    vx = f(vx + fma(K.a, sinf(fma(K.c, y, f(tf * K.d))),
        f(K.b * cosf(fma(K.e, f(x + y), -f(tf * K.g))))));
    vy = f(vy + fma(K.a, cosf(fma(K.h, x, -f(tf * K.i))),
        f(K.b * sinf(fma(K.j, f(x - y), f(tf * K.l))))));
    return [vx, vy];
}

// Palette: mostly silver, with rare low-saturation cool and warm accents.
const SILVER = [234, 240, 248];
const COOL = [196, 214, 230];
const WARM = [228, 210, 182];
const FOG = [224, 231, 240];
const GROUND_GLOW = [34, 42, 56];
const STEPS = 30;

class MarkWriter {
    constructor() {
        this.data = new Float32Array(4096);
        this.length = 0;
        this.count = 0;
    }
    reserve(extra) {
        if (this.length + extra <= this.data.length) return;
        let size = this.data.length * 2;
        while (size < this.length + extra) size *= 2;
        const grown = new Float32Array(size);
        grown.set(this.data.subarray(0, this.length));
        this.data = grown;
    }
    push(kind, a, b, c, [red, green, blue], alpha, middle = 0) {
        this.reserve(MARK_HEADER);
        this.data.set([kind, a, b, c, red / 255, green / 255, blue / 255, alpha / 255, middle / 255],
            this.length);
        this.length += MARK_HEADER;
        this.count += 1;
    }
    dot(x, y, radius, color, alpha) { this.push(MARK_DOT, x, y, radius, color, alpha); }
    // The native `field_glow`: alpha, then 0.32 of it at 0.38 of the radius.
    glow(x, y, radius, color, peak) {
        this.push(MARK_GLOW, x, y, radius, color, peak, byte(f(peak * f(0.32))));
    }
    line(points, width, color, alpha) {
        this.push(MARK_LINE, points.length / 2, width, 0, color, alpha);
        this.reserve(points.length);
        this.data.set(points, this.length);
        this.length += points.length;
    }
    finish() {
        return {marks: this.data.slice(0, this.length), count: this.count};
    }
}

// One frame of the field in the box {x, y, w, h} at time `t`. `flow` 0 to 1
// sets streak length, `density` scales the emitter count, `energy` 0 to 1
// brightens, and `mode` is 0 Tracers, 1 Currents or 2 Field. `hash` replaces
// the seed hash, for tests that seed from another platform's values.
export function fieldMarks({x = 0, y = 0, w, h, t, flow = 0, density = 1, energy = 0, mode = 0,
    hash = fieldHash}) {
    const out = new MarkWriter();
    const cx = fma(w, f(0.5), x);
    const cy = fma(h, f(0.5), y);
    const fx = f(w * f(0.54));
    const fy = f(h * f(0.54));
    const bright = fma(f(0.45), f(energy), f(0.55));

    // A faint atmospheric vignette.
    out.glow(cx, cy, f(Math.max(fx, fy) * f(1.25)), GROUND_GLOW, 16);

    const d = density < 1 ? 1 : Math.trunc(density);
    const slen = fma(f(0.032), f(flow), f(0.006));
    const seed = k => [fma(hash(k * 3 + 1), f(2.3), -f(1.15)), fma(hash(k * 7 + 2), f(2.1), -f(1.05))];
    const advance = (point) => {
        const [vx, vy] = fieldFlowAt(point[0], point[1], t);
        point[0] = fma(vx, slen, point[0]);
        point[1] = fma(vy, slen, point[1]);
    };
    const place = point => [fma(point[0], fx, cx), fma(point[1], fy, cy)];

    if (mode === 2) {
        // FIELD: a continuous illuminated density fog.
        const count = 220 + d * 9;
        for (let k = 0; k < count; ++k) {
            const point = seed(k);
            for (let step = 0; step < 16; ++step) advance(point);
            const gain = f(bright * fma(f(0.6), hash(k * 23 + 8), f(0.7)));
            const radius = fma(f(30), hash(k * 5 + 3), f(26));
            out.glow(...place(point), radius, FOG, Math.trunc(f(f(26) * gain)));
        }
        return out.finish();
    }

    const tracers = 220 + d * 10;
    for (let k = 0; k < tracers; ++k) {
        const point = seed(k);
        const accent = hash(k * 13 + 5);
        const color = accent < f(0.06) ? COOL : accent < f(0.11) ? WARM : SILVER;
        const gain = f(bright * fma(f(0.7), hash(k * 23 + 8), f(0.85)));

        if (mode === 1) {
            // CURRENTS: the integrated streamline, stroked.
            const points = new Float32Array(2 * (STEPS + 1));
            points.set(place(point), 0);
            for (let step = 0; step < STEPS; ++step) {
                advance(point);
                points.set(place(point), 2 * (step + 1));
            }
            out.line(points, f(1.1), color, byte(f(f(55) * gain)));
            continue;
        }

        // TRACERS: a dotted streak, faint tail to bright head.
        for (let step = 0; step < STEPS; ++step) {
            advance(point);
            const fraction = f(step / STEPS);
            const alpha = Math.trunc(f(fma(f(105), fraction, f(14)) * gain));
            out.dot(...place(point), fma(f(1.05), fraction, f(0.5)), color, Math.min(255, alpha));
        }
        const head = place(point);
        out.dot(...head, f(1.6), color, byte(f(f(120) * gain)));
        out.dot(...head, f(0.9), [255, 255, 255], byte(f(f(150) * gain)));
    }
    return out.finish();
}
