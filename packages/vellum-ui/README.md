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

Interactive nodes require explicit stable IDs. Generated DesignIR components
already carry them; hand-written components should choose IDs that survive
refactors and reimports. Event handlers and hook state remain developer-owned
and are never written into generated DesignIR files.

Applications that persist snapshots should pass a stable `id` to `createApp`.
Components normally derive identity from their name and implementation; set a
unique static `vellumId` when state must survive an intentional implementation
replacement or reimport.

Run `npm ci && npm test` to exercise runtime, strict TypeScript/JSX, and
classic-script bundle checks. `npm run build:native-test -- <output.js>` builds
the fixture consumed by the native JavaScriptCore integration test; it is not
an application-facing bundler command.

The native bridge calls `globalThis.__vellum.renderJSON()`,
`dispatchJSON(...)`, `snapshotStateJSON()`, and `restoreStateJSON(...)`. Those
names form a versioned host protocol, not a browser global API for applications
to call directly.
