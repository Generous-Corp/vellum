# Runtime-import parser fixtures

`stitch/transport-bar.tsx` and `rn/gain-stage.tsx` are synthetic React
exports copied from Pulp `test/fixtures/stitch/transport-bar.tsx` and
`test/fixtures/rn/gain-stage.tsx` at `4729c9c0e58a7c0cec336a49deaf93a7cedab65c`. They contain no
design-tool data and make no service calls. The
`vellum.framework.runtime-import-parsers` test reads them to check
`parse_stitch_react` and `parse_react_native_export`.
