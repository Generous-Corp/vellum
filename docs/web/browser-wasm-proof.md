# Browser/Wasm proof

This target answers one narrow architecture question with executable evidence:
can ordinary browser JavaScript use Vellum's retained authoring model while the
same C++ runtime, scene, and paint-command traversal used by the native renderer
run as WebAssembly?

The answer for the current bounded primitive set is yes. Exact-pinned SDK
artifacts can now expose this as an installed static application target.

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

## Installed application lane

Release assembly is deliberately split into two toolchains. First build a
source-commit-bound Wasm payload with an activated Emscripten SDK. Then compose
that payload and an explicit pinned Node executable into the host SDK artifact:

```sh
python3 scripts/build_web_payload.py \
  --output /tmp/vellum-web-payload --source-commit "$(git rev-parse HEAD)"
python3 scripts/build_sdk_artifact.py \
  --web-payload /tmp/vellum-web-payload \
  --node-binary /path/to/exact/node \
  --node-license /path/to/node-distribution/LICENSE \
  --node-provenance /path/to/node-provenance.json \
  --output-dir dist
```

The web payload manifest records the Vellum commit, Emscripten version, backend
identity, file sizes, and SHA-256 hashes. Artifact construction and verification
reject a partial, modified, or source-mismatched payload. Web-capable artifacts
must include SDK-local Node plus its exact license and source/hash provenance;
generated applications do not contain Node. Installed launchers prefer this
SDK-local runtime. Only an explicit local-development install falls back to a
compatible system Node.

After verified installation, no framework checkout or Emscripten installation
is needed:

```sh
vellum create "Web App" --directory web-app
cd web-app
vellum build --target web
vellum test --target web --scenario smoke
vellum run --target web
vellum package --target web --output dist
```

Build bundles application TypeScript/JavaScript with installed exact tooling,
copies the exact installed Wasm/runtime bytes, and scans the Wasm plus a detector
negative control. Test serves the build temporarily and requires a real Chrome
semantic scenario whose press changes the C++ command digest. Package emits a
reproducible static `.tar.gz`. Run returns a local `python -m http.server`
command rather than starting an unmanaged background process.

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
  browser navigation, persistence, deployment automation, or screenshot-driver
  parity in this milestone.
- Static build/test/package is supported, but web capture is not advertised.
- The 16 MiB fixed Wasm memory is adequate for this bounded proof, not a final
  application memory policy.

## Runtime output

The generated application boundary is `.vellum/build/web/`:

```text
.vellum/build/web/
├── app.js                    # browser JavaScript application bundle
├── index.html                # canvas shell
├── vellum_host.js            # generic retained-tree bridge
├── vellum_web_core.js       # ESM module factory
├── vellum_web_core.wasm     # shared C++ runtime/graphics core
└── build-manifest.json      # exact artifact identity and file hashes
```

## Next decision gates

Extract the retained-tree-to-scene materializer into a
portable C++ authoring-core target and prove identical semantic/layout snapshots
for native and browser fixtures. Then select and prove the intended browser GPU
backend. Only after those gates should Vellum claim layout or GPU parity.
