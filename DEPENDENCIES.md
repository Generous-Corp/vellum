# Dependencies

This inventory describes dependencies reachable from Vellum's active source,
build targets, installed SDK, and release artifact at this exact checkpoint.
Historical files reachable only through Git history are not active
dependencies.

## Redistributed third-party material

None.

The current Vellum-owned C++ foundation, runtime, and CoreGraphics proof do not
vendor or redistribute third-party source or binary libraries. The JavaScript
DesignIR package uses Node.js built-ins and declares no npm runtime or
development dependencies.

## Developer-provided prerequisites

These tools and system APIs are required for development but are not bundled or
redistributed by Vellum:

| Prerequisite | Current use |
|---|---|
| CMake 3.24 or newer | Configure, build, install, and package the C++ SDK |
| C++20 compiler | Compile the native SDK |
| Python 3.9 or newer | CLI, artifact construction, verification, and tests |
| Node.js 20 or newer and npm | DesignIR import/reimport backend and tests |
| Xcode command-line tools | Build the current macOS proof |
| Apple Cocoa, CoreGraphics, CoreFoundation, and ImageIO frameworks | System-provided macOS lifecycle and graphics APIs |

## Pending GPU renderer integration

Skia, Dawn, and any transitive renderer dependencies are not present in the
active source tree or current SDK artifact. A later GPU change must update this
inventory and `NOTICE.md`, lock exact artifact and source identities, provide
complete redistributed-license coverage, and pass the source, artifact, and
sterile-install scans before it is release eligible.

## Historical extraction material

The filtered seed once placed 232 projected source and vendored files in the
working tree. Those paths have been deleted from the active tip. Their exact
historical identities remain in `provenance/cut-manifest.json` and Git history;
they are not build inputs, installed files, or release payloads.

The machine-readable current inventory is
`provenance/third-party-lock.json`. The active source boundary is
`provenance/active-source-boundary.json`.
