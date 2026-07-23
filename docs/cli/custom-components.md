# App-owned custom C++ components

Custom components are an optional escape hatch for a bounded visualization or
other paint-heavy primitive. They are application code, not generated Vellum
source and not framework forks. A native build compiles each declared source
into the application bundle and loads it through `vellum.component-abi.v1`.

Declare every module in `native/components.toml`:

```toml
[manifest]
schema = "vellum.components.v1"
components = ["level-meter"]

[component.level-meter]
native_source = "native/level-meter.cpp"
web = "fallback"
```

`web = "fallback"` means the JSX fallback is the explicit browser behavior.
`web = "wasm"` instead requires a `wasm_source`. The installed web backend
links that source with Vellum's browser ABI adapter and packages an ES module
and Wasm file with the static application:

```toml
[component.level-meter]
native_source = "native/level-meter.cpp"
web = "wasm"
wasm_source = "native/level-meter.cpp"
```

The native and browser sources may be the same ABI-clean file or separate
implementations. An activated Emscripten SDK providing `em++` is a build
prerequisite. Vellum discovers it through `EMSDK`, `~/emsdk`, or `PATH`.

Build and exercise the installed browser application normally:

```sh
vellum build --target web
vellum test --target web --scenario smoke
vellum package --target web
```

The standalone proof tool remains useful for checking a component without
building an application:

```sh
python3 scripts/verify_component_wasm.py --project path/to/app --json
```

For `web = "wasm"`, descriptor mismatches, missing compilers, malformed paint
commands, empty output, and missing installed adapter/header files fail the
build or scenario. Vellum never silently substitutes the JSX fallback. The
fallback remains explicit behavior only for `web = "fallback"`.

[`product/component-support.yaml`](../../product/component-support.yaml) is the
machine-readable claim boundary.

Use the component from TypeScript or JavaScript:

```tsx
<CustomComponent
  id="meter"
  component="level-meter"
  properties={{ values: [0.2, 0.7, 0.4] }}
  style={{ width: 320, height: 120 }}
  fallback={<View id="meter-fallback" style={{ width: 320, height: 120 }} />}
/>
```

The source includes only `<vellum/components/abi.h>`, exports
`vellum_component_entry_v1`, and emits bounded rectangle or text paint
commands. It does not link Vellum libraries or use renderer, scene, Skia, Dawn,
or Pulp headers. The backend rejects undeclared modules, component IDs that do
not match their descriptor, other Vellum headers, unknown manifest fields,
duplicate commands, malformed bounds/colors, and ABI mismatches.

`vellum build` owns the generated compile invocation under `.vellum/build` and
puts the resulting dylib in `Contents/PlugIns/VellumComponents`. The editable
source and declaration stay in the application repository. `run`, `test`,
`capture`, and `package` use the same bundled module. Run `vellum doctor`
after declaring one: it verifies that Xcode's selected `clang++` and macOS SDK
are both available. The backend selects that SDK itself; applications do not
set `SDKROOT` or maintain compiler flags.

Browser scenarios support the same bounded semantic action vocabulary used by
the native scenario lane: wait, capture evidence, press/click, controlled text
input, and the documented semantic keys. This is interaction-contract parity;
it does not claim native persistence or the native PNG capture/montage command
on the web.
