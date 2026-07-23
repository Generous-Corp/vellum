# `@vellum/ui`

This package is Vellum's dependency-free JavaScript/TypeScript/JSX authoring
surface. It produces a deterministic, serializable retained tree rather than a
DOM. The same bundle can run inside Vellum's native JavaScript host or in a
browser that is driving the shared Wasm core.

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

The native bridge calls `globalThis.__vellum.renderJSON()`,
`dispatchJSON(...)`, `snapshotStateJSON()`, and `restoreStateJSON(...)`. Those
names form a versioned host protocol, not a browser global API for applications
to call directly.
