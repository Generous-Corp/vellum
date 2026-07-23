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
`web = "wasm"` instead requires a separate `wasm_source`. The Wasm proof tool
compiles that app-owned source with Emscripten and executes it through the same
descriptor, render-context, and paint-command ABI:

```sh
python3 scripts/verify_component_wasm.py --project path/to/app --json
```

That is real Wasm execution evidence for the extension ABI. It is not yet a
claim that the installed browser application backend packages the module; the
browser target remains an incubation lane.

[`product/component-support.yaml`](../../product/component-support.yaml) is the
machine-readable claim boundary. In particular, `vellum build --target web`
does not yet incorporate app-owned Wasm components.

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
`capture`, and `package` use the same bundled module.
