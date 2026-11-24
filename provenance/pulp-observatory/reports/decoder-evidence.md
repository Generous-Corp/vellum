# Decoder resolution evidence

This supplements five decoder resolutions without changing their dispositions.
The original events remain immutable. Later `-evidence` resolution events select
this evidence through the observatory's existing resolution-chain rules.

All source and results below refer to Pulp commit
`b0ee92d21944d5c956603afd1108b575e50c1302`, the snapshot named in the original
events. They make no claim about later source or deployed binaries.

## Implementation

- Shared decompression budget, observation
  `vellum-a5f66ca2279f4534f5d3d2797f6fe7cf3f689375`:
  [container.mjs](https://github.com/Generous-Corp/pulp/blob/b0ee92d21944d5c956603afd1108b575e50c1302/tools/import-design/fig/container.mjs)
  creates one `budget` in `unpackFig`, passes it to `unzip`, then passes the
  same object to both `decompressChunk` calls. `inflateEntry` and
  `decompressChunk` bound output by `budget.remaining` and subtract output
  length. `decompressChunk` rejects an exhausted budget before invoking zlib.
- Stored ZIP entries, observation
  `vellum-2e89dab24d2033e83673c75039f696b7769867d3`:
  the same implementation calls `chargeStored` from both the local-header and
  central-directory paths. That function rejects `raw.length > budget.remaining`
  and subtracts the length before copying the payload. The central-directory
  fallback restores the budget before replaying entries, avoiding double charges.
- Frame selection, observation
  `vellum-bd34f13f5aadd560cac7edf6881fe867238ff1ac`:
  [fig_decode.mjs](https://github.com/Generous-Corp/pulp/blob/b0ee92d21944d5c956603afd1108b575e50c1302/tools/import-design/fig_decode.mjs)
  calls `outline` from `cmdOutline`. `cmdEmit` recognizes a GUID by membership
  in `scene.byGuid`. Otherwise it obtains `framesByName` and calls `fail(..., 2)`
  before `findFrame` when multiple frames match. The nested-node fallback has
  its own `nodesByName` ambiguity check. Thus the CLI refuses ambiguity before
  creating output, while an explicit GUID selects a frame.

## Pinned source excerpts

These excerpts are copied from the pinned commit above. Line numbers refer to
that commit. The behavioral checks below exercise these paths.

`tools/import-design/fig/container.mjs`, lines 22 through 32:

```js
function chargeStored(raw, budget) {
  if (raw.length > budget.remaining) throw new RangeError('cumulative decompression budget exceeded');
  budget.remaining -= raw.length;
  return Buffer.from(raw);
}

function inflateEntry(raw, budget) {
  const out = inflateRawSync(raw, { maxOutputLength: Math.min(MAX_DECOMPRESSED_BYTES, budget.remaining) });
  budget.remaining -= out.length;
  return out;
}
```

`tools/import-design/fig/container.mjs`, lines 124 through 134:

```js
export function unpackFig(fileBytes) {
  const budget = { remaining: MAX_TOTAL_DECOMPRESSED_BYTES };
  let canvas = fileBytes;
  const images = new Map();
  let meta = null;
  if (isZip(fileBytes)) {
    const entries = unzip(fileBytes, budget);
    const canvasName = Object.keys(entries).find((n) => n.endsWith('.fig'));
    if (!canvasName) throw new Error('.fig ZIP has no inner canvas .fig entry');
    canvas = entries[canvasName];
    for (const [name, bytes] of Object.entries(entries)) {
```

`tools/import-design/fig/container.mjs`, lines 164 through 182:

```js
    version,
    schemaBytes: decompressChunk(chunks[0], budget),
    messageBytes: decompressChunk(chunks[1], budget),
    images,
    meta,
  };
}

function decompressChunk(chunk, budget) {
  if (budget.remaining <= 0) throw new RangeError('cumulative decompression budget exceeded');
  if (chunk.length >= 4 && chunk.readUInt32LE(0) === ZSTD_MAGIC) {
    const out = zstdDecompressSync(chunk, { maxOutputLength: Math.min(MAX_DECOMPRESSED_BYTES, budget.remaining) });
    budget.remaining -= out.length;
    return out;
  }
  const out = inflateRawSync(chunk, { maxOutputLength: Math.min(MAX_DECOMPRESSED_BYTES, budget.remaining) });
  budget.remaining -= out.length;
  return out;
}
```

`tools/import-design/fig_decode.mjs`, lines 84 through 108:

```js
  // wrong one — fail with the candidates so the caller picks one by guid or by
  // --page. Decide guid-ness by membership, exactly as findFrame does: a frame
  // name can itself contain a colon ("16:9"), so a substring `:` test would
  // wrongly treat such a name as a guid and skip this gate.
  const isGuid = scene.byGuid.has(opts.frame);
  if (!isGuid) {
    const matches = framesByName(scene, opts.frame, opts.page);
    if (matches.length > 1) {
      const list = matches.map((m) => `  ${m.guid}  (page: ${m.page})`).join('\n');
      const scope = opts.page ? ` on page '${opts.page}'` : '';
      // A guid always disambiguates; --page only helps when the matches span
      // more than one page, so only suggest it then.
      const spanPages = new Set(matches.map((m) => m.page)).size > 1;
      const hint = spanPages
        ? 'Re-run with its guid (--frame <guid>), or narrow with --page <name>.'
        : 'Re-run with its guid (--frame <guid>).';
      fail(
        `frame name '${opts.frame}'${scope} is ambiguous — ${matches.length} frames match:\n` +
        `${list}\n${hint}`,
        2,
      );
    }
    // No top-level frame matches: findFrame falls back to any nested node with
    // this name. Guard that fallback too so a name shared by several nested nodes
    // is not silently resolved to the first. (A page restriction only scopes
```

## Reproduction and controls

Run the evidence program with Node supporting `zstdDecompressSync` and a local
Pulp repository containing the pinned commit. The second argument must be a new
directory under the caller's permitted scratch root:

```sh
node provenance/pulp-observatory/reports/decoder-evidence.mjs "$PULP_REPO" "$EVIDENCE_SCRATCH"
```

The program reads committed blobs with `git show`, leaving the source checkout
untouched. It extracts the decoder, generator and fixture to the supplied scratch
directory. It runs eight of the pinned upstream tests, including cumulative ZIP
decompression and overlapping stored central-directory records. It also generates
a file with two identically named frames and runs the actual decoder CLI.

The budget probes change only the total cap from 512 MiB to 128 bytes in an
in-memory module, so fixtures remain small. They exercise positive and rejecting
boundaries for shared inner chunks, ZIP plus inner chunks, and stored local
entries. These are scaled boundary tests, not a measurement at the production
512 MiB limit. The upstream ZIP tests and CLI execute unmodified pinned source.

Recorded result using Node v26.13.0:

```text
quoted searches: zlib import=1, leading-close guard=0, toPathData control=1
PASS shared inner-chunk, ZIP/inner-chunk, and stored-entry boundaries
KILLED separate inner budget
KILLED separate ZIP budget
KILLED uncharged stored entries
upstream tests: 8 passed, 0 failed
PASS real CLI outline, ambiguous-name rejection, explicit-guid control; KILLED removed ambiguity guard
PASS pinned decoder evidence at b0ee92d21944d5c956603afd1108b575e50c1302
```

Each budget mutation must cause its boundary assertion to fail. Removing the
CLI's multiple-frame guard must change the duplicate-name invocation from exit 2
to exit 0. The original CLI is restored after that control. This demonstrates
that the tested enforcement paths, rather than identifier counts, cause rejection.
The nested ambiguity path is inspected in source and its lookup is covered by an
upstream unit test; this report does not claim a separate nested CLI mutation test.

## Corrected literal searches

The old events nested single quotes, causing the shell to remove quotes that
belong to the search text. The later evidence events replace those commands.
Double quotes outside preserve the literal single quotes around `node:zlib` and
`Z`:

```sh
git -C "$PULP_REPO" grep -c -F -e "import { inflateRawSync, zstdDecompressSync } from 'node:zlib'" b0ee92d21944d5c956603afd1108b575e50c1302 -- tools/import-design/fig/container.mjs
git -C "$PULP_REPO" grep -c -F -e "op !== 'Z'" b0ee92d21944d5c956603afd1108b575e50c1302 -- tools/import-design/fig/paths.mjs
git -C "$PULP_REPO" grep -c -F -e 'export function toPathData' b0ee92d21944d5c956603afd1108b575e50c1302 -- tools/import-design/fig/paths.mjs
```

Results are respectively one matching line (exit 0), no matching line (exit 1),
and one matching line (exit 0). The executable evidence independently asserts
those counts on the exact source strings. The zstd-import and leading-close
observations remain `port-required`; these corrected searches do not resolve
either outstanding port.
