// The perceptual and ink gate shared by the Vellum editor fixtures and the
// native parity fixture. It works on plain RGBA buffers ({width, height,
// data}), so it runs in the browser on canvas pixels and in Node on test
// images.
//
// Ink. A region has ink when its pixels differ from its surface: the same
// frame, at the same frozen time, drawn without the region's own marks. The
// surface is whatever lies under the region (a chip, the field, the ground),
// so a label inside a chip is measured against the chip and the field behind
// it, never against the editor background, and a label that is missing or
// drawn in the surface colour has none. A pixel is ink when it is more than
// INK_DISTANCE from the surface pixel in RGB. A region passes when at least
// INK_FLOOR of its area is ink: the plan's new 1.0% per-region floor.
//
// Perception. SSIM (Wang et al. 2004) on luma with an 11 tap Gaussian window
// of sigma 1.5 and the standard C1 = (0.01 L)^2, C2 = (0.03 L)^2, L = 255,
// averaged over every full window. A device-scale-2 capture is reduced to
// the luma of each 2 by 2 block first, the way the native reference is
// (`make_native_reference.py`). The plan's new floor is SSIM_FLOOR.

// RGB distance above which a pixel is ink. The session's existing ink
// threshold; DESIGN.
export const INK_DISTANCE = 8;
// The plan's per-region ink floor, as a fraction of the region's area. DESIGN.
export const INK_FLOOR = 0.01;
// The plan's whole-image SSIM floor. DESIGN.
export const SSIM_FLOOR = 0.90;

const WINDOW = 11;
const SIGMA = 1.5;
const C1 = (0.01 * 255) ** 2;
const C2 = (0.03 * 255) ** 2;

export function luma(red, green, blue) {
    return 0.299 * red + 0.587 * green + 0.114 * blue;
}

// The luma plane of an RGBA image, as {width, height, data: Float64Array}.
export function lumaPlane(image) {
    const data = new Float64Array(image.width * image.height);
    for (let index = 0; index < data.length; ++index) {
        data[index] = luma(image.data[4 * index], image.data[4 * index + 1],
            image.data[4 * index + 2]);
    }
    return {width: image.width, height: image.height, data};
}

// The luma of each 2 by 2 block of an RGBA image, rounded to whole steps as
// the reference's 8-bit greyscale stores it.
export function reduceLuma(image) {
    const width = Math.floor(image.width / 2);
    const height = Math.floor(image.height / 2);
    const data = new Float64Array(width * height);
    for (let y = 0; y < height; ++y) {
        for (let x = 0; x < width; ++x) {
            let total = 0;
            for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
                const at = 4 * ((2 * y + dy) * image.width + 2 * x + dx);
                total += luma(image.data[at], image.data[at + 1], image.data[at + 2]);
            }
            data[y * width + x] = Math.min(255, Math.floor(total / 4 + 0.5));
        }
    }
    return {width, height, data};
}

function gaussian() {
    const half = (WINDOW - 1) / 2;
    const weights = Array.from({length: WINDOW}, (_, index) =>
        Math.exp(-((index - half) ** 2) / (2 * SIGMA * SIGMA)));
    const total = weights.reduce((sum, value) => sum + value, 0);
    return weights.map(value => value / total);
}

// The window-weighted mean of `plane` at every full window, separably.
function blur(plane, width, height, weights) {
    const outWidth = width - WINDOW + 1;
    const outHeight = height - WINDOW + 1;
    const rows = new Float64Array(outWidth * height);
    for (let y = 0; y < height; ++y) {
        for (let x = 0; x < outWidth; ++x) {
            let sum = 0;
            for (let k = 0; k < WINDOW; ++k) sum += weights[k] * plane[y * width + x + k];
            rows[y * outWidth + x] = sum;
        }
    }
    const out = new Float64Array(outWidth * outHeight);
    for (let y = 0; y < outHeight; ++y) {
        for (let x = 0; x < outWidth; ++x) {
            let sum = 0;
            for (let k = 0; k < WINDOW; ++k) sum += weights[k] * rows[(y + k) * outWidth + x];
            out[y * outWidth + x] = sum;
        }
    }
    return out;
}

// Mean SSIM of two luma planes of one size.
export function ssim(a, b) {
    if (a.width !== b.width || a.height !== b.height) {
        throw new Error(`SSIM of ${a.width}x${a.height} against ${b.width}x${b.height}`);
    }
    if (a.width < WINDOW || a.height < WINDOW) throw new Error('image smaller than the SSIM window');
    const {width, height} = a;
    const weights = gaussian();
    const product = (left, right) => left.map((value, index) => value * right[index]);
    const muA = blur(a.data, width, height, weights);
    const muB = blur(b.data, width, height, weights);
    const aa = blur(product(a.data, a.data), width, height, weights);
    const bb = blur(product(b.data, b.data), width, height, weights);
    const ab = blur(product(a.data, b.data), width, height, weights);
    let total = 0;
    for (let index = 0; index < muA.length; ++index) {
        const ma = muA[index];
        const mb = muB[index];
        const va = aa[index] - ma * ma;
        const vb = bb[index] - mb * mb;
        const cov = ab[index] - ma * mb;
        total += ((2 * ma * mb + C1) * (2 * cov + C2)) /
            ((ma * ma + mb * mb + C1) * (va + vb + C2));
    }
    return total / muA.length;
}

// The ink of one region: pixels of `frame` farther than INK_DISTANCE from
// the same pixels of `surface`. Both are RGBA crops of the region.
export function regionInk(frame, surface) {
    if (frame.width !== surface.width || frame.height !== surface.height) {
        throw new Error('a region and its surface differ in size');
    }
    let pixels = 0;
    for (let index = 0; index < frame.data.length; index += 4) {
        const distance = Math.hypot(frame.data[index] - surface.data[index],
            frame.data[index + 1] - surface.data[index + 1],
            frame.data[index + 2] - surface.data[index + 2]);
        if (distance > INK_DISTANCE) pixels += 1;
    }
    const area = frame.width * frame.height;
    return {pixels, area, fraction: area ? pixels / area : 0, pass: area > 0 && pixels >= INK_FLOOR * area};
}

// The gate's verdict. `ink` maps region names to `regionInk` results and
// `similarity` is an SSIM, or null when no reference was compared. Failures
// name each region below the ink floor and the SSIM when it is below
// SSIM_FLOOR.
export function verdict({ink = {}, similarity = null}) {
    const failures = Object.entries(ink).filter(([, result]) => !result.pass)
        .map(([name]) => `ink:${name}`);
    if (similarity !== null && !(similarity >= SSIM_FLOOR)) failures.push('ssim');
    return {pass: failures.length === 0, failures, similarity, ink};
}
