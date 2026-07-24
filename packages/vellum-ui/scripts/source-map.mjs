import { readFile, writeFile } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';

export const SOURCE_MAP_SCHEMA = 'vellum.source-map.v1';

const BASE64 = new Map(
    [...'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/']
        .map((character, index) => [character, index]),
);

function decodeVlq(value, cursor) {
    let result = 0;
    let shift = 0;
    let continuation = true;
    while (continuation) {
        if (cursor.index >= value.length) throw new Error('truncated VLQ value');
        const digit = BASE64.get(value[cursor.index++]);
        if (digit === undefined) throw new Error('invalid base64 VLQ character');
        continuation = (digit & 32) !== 0;
        result += (digit & 31) << shift;
        shift += 5;
        if (shift > 30) throw new Error('source-map VLQ value exceeds 30 bits');
    }
    const negative = (result & 1) !== 0;
    result >>>= 1;
    return negative ? -result : result;
}

export function decodeMappings(mappings, sourceCount, nameCount) {
    if (typeof mappings !== 'string') throw new Error('source-map mappings must be a string');
    const lines = [];
    let source = 0;
    let originalLine = 0;
    let originalColumn = 0;
    let name = 0;
    for (const encodedLine of mappings.split(';')) {
        let generatedColumn = 0;
        const decodedLine = [];
        for (const encodedSegment of encodedLine.split(',')) {
            if (!encodedSegment) continue;
            const cursor = { index: 0 };
            generatedColumn += decodeVlq(encodedSegment, cursor);
            if (generatedColumn < 0) throw new Error('negative generated source-map column');
            if (cursor.index === encodedSegment.length) {
                decodedLine.push([generatedColumn]);
                continue;
            }
            source += decodeVlq(encodedSegment, cursor);
            originalLine += decodeVlq(encodedSegment, cursor);
            originalColumn += decodeVlq(encodedSegment, cursor);
            if (source < 0 || source >= sourceCount ||
                originalLine < 0 || originalColumn < 0) {
                throw new Error('source-map segment is out of range');
            }
            const segment = [generatedColumn, source, originalLine, originalColumn];
            if (cursor.index < encodedSegment.length) {
                name += decodeVlq(encodedSegment, cursor);
                if (name < 0 || name >= nameCount) {
                    throw new Error('source-map name is out of range');
                }
                segment.push(name);
            }
            if (cursor.index !== encodedSegment.length) {
                throw new Error('source-map segment has unexpected fields');
            }
            decodedLine.push(segment);
        }
        lines.push(decodedLine);
    }
    return lines;
}

function within(root, candidate) {
    const value = relative(root, candidate);
    return value === '' || (!value.startsWith(`..${sep}`) && value !== '..' && !value.startsWith(sep));
}

function canonicalPath(value) {
    try {
        return realpathSync.native(value);
    } catch {
        return resolve(value);
    }
}

function uriPath(value) {
    return value.split(sep).map(encodeURIComponent).join('/');
}

export function normalizeSourceMap(raw, {
    mapPath,
    projectRoot,
    packageRoot,
} = {}) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw) || raw.version !== 3 ||
        !Array.isArray(raw.sources) || !Array.isArray(raw.sourcesContent) ||
        raw.sources.length !== raw.sourcesContent.length ||
        !Array.isArray(raw.names) || typeof raw.mappings !== 'string') {
        throw new Error('malformed source map');
    }
    if (raw.sections !== undefined) throw new Error('indexed source maps are unsupported');
    projectRoot = canonicalPath(projectRoot);
    packageRoot = canonicalPath(packageRoot);
    const sourceBase = resolve(dirname(mapPath), raw.sourceRoot ?? '.');
    const sources = raw.sources.map((item) => {
        if (typeof item !== 'string' || item.length === 0 || item.includes('\0')) {
            throw new Error('source-map source must be a non-empty path');
        }
        const absolute = canonicalPath(resolve(sourceBase, item));
        if (within(projectRoot, absolute)) {
            return `vellum://app/${uriPath(relative(projectRoot, absolute))}`;
        }
        if (within(packageRoot, absolute)) {
            return `vellum://sdk/ui/${uriPath(relative(packageRoot, absolute))}`;
        }
        return `vellum://external/${encodeURIComponent(item)}`;
    });
    const lines = decodeMappings(raw.mappings, sources.length, raw.names.length);
    return {
        schema: SOURCE_MAP_SCHEMA,
        version: 1,
        sources,
        sourcesContent: raw.sourcesContent,
        names: raw.names,
        lines,
    };
}

// esbuild labels every bundled file with a leading `// <path>` comment. When the
// SDK is consumed from an install prefix those labels embed the install
// location, which makes the generated bundle non-relocatable. Blank the comment
// text in place and keep its newline: the runtime footer resolves stack frames
// through generated line and column numbers, so removing the line itself would
// shift every mapping below it out of alignment.
const SDK_INSTALL_PATH_COMMENT = /^\/\/ [^\r\n]*[\\/]vellum-installs[\\/][^\r\n]*(?=\r?$)/gm;

export function blankSdkInstallPathComments(bundle) {
    return bundle.replace(SDK_INSTALL_PATH_COMMENT, '');
}

export function runtimeFooter(map) {
    if (map?.schema !== SOURCE_MAP_SCHEMA || !Array.isArray(map.lines)) {
        throw new Error('cannot emit malformed Vellum source map');
    }
    const encoded = JSON.stringify({
        schema: map.schema,
        version: map.version,
        sources: map.sources,
        names: map.names,
        lines: map.lines,
    });
    return `
;(() => {
  "use strict";
  const map = ${encoded};
  const framePattern = /^(?:\\s*at\\s+)?(?:(.*?)@|(?:(.*?)\\s+\\()?)(.*?):(\\d+):(\\d+)\\)?$/;
  function mappedFrame(line) {
    const match = String(line).match(framePattern);
    if (!match) return null;
    const generatedLine = Number(match[4]);
    const generatedColumn = Number(match[5]);
    const segments = map.lines[generatedLine - 1];
    if (!Array.isArray(segments)) return null;
    let selected = null;
    for (const segment of segments) {
      if (segment.length >= 4 && segment[0] <= generatedColumn - 1) selected = segment;
      else if (segment[0] > generatedColumn - 1) break;
    }
    if (!selected) return null;
    const file = map.sources[selected[1]];
    if (typeof file !== "string" || !file.startsWith("vellum://app/")) return null;
    const frame = {file, line: selected[2] + 1, column: selected[3] + 1};
    const name = selected.length > 4 ? map.names[selected[4]] : (match[1] || match[2]);
    if (typeof name === "string" && name && name !== "global code") frame.function = name;
    return frame;
  }
  Object.defineProperty(globalThis, "__vellumMapExceptionJSON", {
    configurable: false,
    enumerable: false,
    writable: false,
    value(error) {
      const message = error && typeof error.message === "string"
        ? error.message : String(error);
      const frames = String(error && error.stack || "").split("\\n")
        .map(mappedFrame).filter(Boolean);
      if (frames.length === 0) {
        throw new Error("VELLUM_SOURCE_MAP_NO_APPLICATION_FRAME");
      }
      return JSON.stringify({
        protocol: "vellum.authoring-host.v2",
        kind: "diagnostic",
        severity: "error",
        code: "VELLUM_RUNTIME_EXCEPTION",
        message,
        source: frames[0],
        stack: frames,
      });
    },
  });
})();
`;
}

export async function finalizeBundleSourceMap({
    bundlePath,
    projectRoot,
    packageRoot,
}) {
    const mapPath = `${bundlePath}.map`;
    let raw;
    try {
        raw = JSON.parse(await readFile(mapPath, 'utf8'));
    } catch (error) {
        throw new Error(`VELLUM_SOURCE_MAP_MISSING_OR_MALFORMED: ${error.message}`, { cause: error });
    }
    const normalized = normalizeSourceMap(raw, {
        mapPath,
        projectRoot: resolve(projectRoot),
        packageRoot: resolve(packageRoot),
    });
    let bundle = await readFile(bundlePath, 'utf8');
    bundle = bundle.replace(/\n?\/\/# sourceMappingURL=.*?(?:\n|$)/g, '\n');
    bundle = blankSdkInstallPathComments(bundle);
    const footer = runtimeFooter(normalized);
    const mapName = encodeURIComponent(bundlePath.split(/[\\/]/).at(-1) + '.map');
    await writeFile(bundlePath, `${bundle}${footer}//# sourceMappingURL=${mapName}\n`);
    await writeFile(mapPath, `${JSON.stringify({
        version: 3,
        sources: normalized.sources,
        sourcesContent: normalized.sourcesContent,
        names: raw.names,
        mappings: raw.mappings,
    })}\n`);
    return { mapPath, normalized };
}
