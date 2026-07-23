# Browser/Wasm proof

This target answers one narrow architecture question with executable evidence:
can ordinary browser JavaScript use Vellum's retained authoring model while the
same C++ runtime, scene, and paint-command traversal used by the native renderer
run as WebAssembly?

The answer for the current bounded primitive set is yes. This is an incubation
target, not a supported application or packaging target yet.

## Build and run

Prerequisites are CMake, Ninja, Python 3.9 or newer, an activated Emscripten SDK,
and Google Chrome for the executable browser test. The known proof environment
uses Emscripten 6.0.2.

```sh
source /path/to/emsdk/emsdk_env.sh
emcmake cmake -S . -B build-web -G Ninja \
  -DVELLUM_ENABLE_WEB=ON \
  -DVELLUM_ENABLE_GPU=OFF \
  -DVELLUM_ENABLE_AUTHORING=OFF \
  -DVELLUM_BUILD_SMOKE_NATIVE=OFF
cmake --build build-web --target vellum-web-core vellum-paint-command-test
ctest --test-dir build-web -R 'vellum\.(paint-command|web\.)' --output-on-failure
python3 -m http.server --directory build-web/web-dist 8000
```

Open `http://127.0.0.1:8000`. The demo runs `@vellum/ui` in browser JavaScript,
starts `vellum::runtime::Kernel` compiled to Wasm, lowers the retained tree into
`vellum::graphics::Scene`, and invokes the shared C++ paint-command traversal.
The target-specific shell presents those commands on one HTML canvas. Clicking
the button dispatches through the versioned authoring bridge and rerenders the
changed state through Wasm.

`vellum.web.browser-smoke` launches real Chrome, waits for an in-page evidence
handshake, and requires a changed shared-core digest after an interaction plus
a nontrivial PNG data result. `vellum.web.no-engine` scans the produced `.wasm`
for known embedded-engine markers. Its negative control proves the detector can
fail when a JavaScriptCore marker is present.

## Exact boundary

What this proves:

- browser JavaScript can run the same dependency-free JSX/state runtime used by
  native authored applications;
- Vellum's process-independent C++ runtime, retained `Scene`, and absolute paint
  command traversal compile and execute as Wasm;
- the native Skia/Dawn renderer and browser shell consume the same C++ command
  semantics rather than maintaining separate retained-tree walkers;
- browser-side state changes can produce different C++ command output;
- the Wasm does not embed JavaScriptCore, QuickJS, or V8 markers.

What this does not prove:

- The browser backend is Canvas2D, not Skia/Dawn/WebGPU and not evidence of GPU
  backend parity. The browser still GPU-accelerates canvas at its discretion,
  but Vellum makes no claim about that here.
- The small browser host currently lowers the supported style subset into scene
  nodes in JavaScript. Native layout materialization is still in the native
  authoring host. Moving that layout/materialization boundary into portable C++
  is required before cross-target layout parity can be claimed.
- There is no arbitrary DOM, CSS, React DOM, HTML import, accessibility tree,
  browser navigation, persistence, packaging, deployment, or screenshot-driver
  parity in this milestone.
- `vellum build`, `run`, `test`, and `package --target web` remain unavailable.
  The installed backend contract is still honestly macOS-only; this proof should
  inform later CLI work rather than advertise capabilities that do not exist.
- The 16 MiB fixed Wasm memory is adequate for this bounded proof, not a final
  application memory policy.

## Reuse from a sterile browser application

The reusable output boundary is `build-web/web-dist/`:

```text
web-dist/
├── vellum_web_core.js       # ESM module factory
├── vellum_web_core.wasm     # shared C++ runtime/graphics core
└── vellum-ui/runtime.js     # browser JavaScript authoring runtime
```

`demo.js`, `index.html`, and `style.css` are a replaceable example shell. An
external experiment can copy or serve the three runtime artifacts, author an
application with the package runtime, and adapt the explicit C ABI used by
`demo.js`. That interface is experimental and exact-pin only. It is intentionally
not installed as a stable SDK or presented as the final application template.

## Next decision gates

Before CLI integration, extract the retained-tree-to-scene materializer into a
portable C++ authoring-core target and prove identical semantic/layout snapshots
for native and browser fixtures. Then select and prove the intended browser GPU
backend. Only after those gates should a web backend advertise build/run/test or
packaging capabilities through the installed CLI protocol.
