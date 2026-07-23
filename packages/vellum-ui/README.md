# `@vellum/ui`

This package is Vellum's dependency-free JavaScript/TypeScript/JSX authoring
surface. It produces a deterministic, serializable retained tree rather than a
DOM. A pinned build proof produces a classic-script bundle for Vellum's native
JavaScriptCore host. Browser JavaScript driving the shared Wasm core remains a
planned validation lane, not a shipped claim.

```tsx
import { Button, Stack, Text, mount, useState } from "@vellum/ui";

function App() {
  const [count, setCount] = useState(0);
  return (
    <Stack id="counter" style={{ width: 320, height: 180, gap: 16 }}>
      <Text id="count">Count: {count}</Text>
      <Button id="increment" onPress={() => setCount(count + 1)}>
        Increment
      </Button>
    </Stack>
  );
}

mount(App);
```

The controlled `TextInput` v1 primitive keeps value ownership in application
code and uses the same serializable event boundary on native and web hosts:

```tsx
import { TextInput, useState } from "@vellum/ui";

const [title, setTitle] = useState("Draft");
<TextInput
  id="board-title"
  value={title}
  placeholder="Board title"
  onChange={(event) => setTitle(event.value)}
  onSubmit={() => save(title)}
/>
```

`TextInput` requires a stable ID, a string `value`, and `onChange`; it rejects
children and unsupported primitive versions. The macOS host currently provides
pointer focus, direct keyboard text insertion, a final-grapheme Backspace, and
bounded semantic key/submit dispatch. It does not yet provide a caret or
selection model, IME composition, clipboard editing shortcuts, password input,
accessibility text semantics, spellcheck, or mobile platform integration.

`CustomComponent` is the explicit bridge to an app-owned C/C++ paint module.
Its `component` name must be declared in `native/components.toml`, its
`properties` are bounded JSON, and its `fallback` remains ordinary Vellum UI
for targets without that module. The SDK's custom-component guide defines the
versioned ABI and source-ownership contract.

Imported DesignIR stays inspectable JSON. Applications opt into it from
developer-owned code. Generated binding receipts resolve stable design
identities after reimport, while the named behavior stays in authored TSX:

```tsx
import { importedBindings, importedDesign } from "@vellum/imported";
import { Design, useState } from "@vellum/ui";

export function App() {
  const [boards, setBoards] = useState(0);
  return (
    <Design
      document={importedDesign}
      bindings={importedBindings}
      actions={{
        "boards.create": () => setBoards((count) => count + 1),
      }}
    />
  );
}
```

The developer-owned overlay binds a design identity to `boards.create`.
Reimport may resolve that binding to a reviewed alias and rewrites only the
generated receipt; the action registry and state remain untouched. A receipt
whose action is absent from the registry or whose resolved node no longer
exists fails closed.

Interactive nodes require explicit stable IDs. Generated DesignIR components
already carry them; hand-written components should choose IDs that survive
refactors and reimports. Event handlers and hook state remain developer-owned
and are never written into generated DesignIR files.

Applications that persist snapshots should pass a stable `id` and
`stateVersion` to `createApp`; incompatible versions fail closed so the
application can run an explicit migration before restore.
Components normally derive identity from their name and implementation; set a
unique static `vellumId` when state must survive an intentional implementation
replacement or reimport.

## Portable application builds

`scripts/build-project.mjs` runs the transitive module graph through
`scripts/check-portability.mjs` before bundling. Failures emit one
`vellum.portability-diagnostics.v1` JSON object covering Node builtins, DOM
globals, dynamic code, non-static dynamic imports, undeclared service
capabilities, platform-only imports, and resolution failures. Native builds
default to an IIFE; set `VELLUM_BUILD_FORMAT=esm` for the web ESM output.

Native persistence is separately capability-gated in `app.toml` with
`persistence = "state-v1"` under `[capabilities]`. On macOS this restores the
whole versioned Vellum snapshot from Application Support before the first
interactive render and atomically rewrites it after a handled mutation. `none`
remains the default. A corrupt, oversized, wrong-app, wrong-state-version, or
incompatible-layout snapshot fails closed. This is not a general key/value
database or migration, sync, secret-storage, or file API.

Run `npm ci && npm test` to exercise runtime, strict TypeScript/JSX, and
classic-script bundle checks. `npm run build:native-test -- <output.js>` builds
a fixture suitable for the repository's native JavaScriptCore integration lane;
this package-level check itself executes under Node and is not an
application-facing bundler command.

The native bridge calls `globalThis.__vellum.renderJSON()`,
`dispatchJSON(...)`, `snapshotStateJSON()`, and `restoreStateJSON(...)`. Those
names form a versioned host protocol, not a browser global API for applications
to call directly.
