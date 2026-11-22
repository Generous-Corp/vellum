# Pulp design cutover map

The three rows map to Vellum owners as follows.

| Pulp row | Pulp capability | Vellum owner | Cutover shape |
| --- | --- | --- | --- |
| C2 | `tools/import-design/` and the `pulp-import-design` executable | `packages/vellum-design-ir/`, `authoring/design_pipeline/`, and the installed `vellum` import backend in `cli/vellum_cli.py` | Keep the import executable contract at the Vellum CLI boundary. Move source adapters and DesignIR normalization into the Vellum design-import package, with the native pipeline as the C++ consumer. |
| C7 | `test/fixtures/emitter-conformance/` and `test/test_design_emitter_conformance.cpp` | `framework/core/view/src/design_import*`, `framework/core/view/src/design_*_codegen.cpp`, and `authoring/design_pipeline/tests/` | Keep one DesignIR fixture set and compare the native materializer, bridge JavaScript, baked C++, and SwiftUI lowerings in Vellum's design-import test lane. |
| C14 | `tools/cli/cmd_design_subcommands.cpp`, `tools/design/pulp_design_debug.cpp`, and `tools/ui-build/` | `cli/vellum_cli.py`, `packages/vellum-design-ir/bin/`, `packages/vellum-ui/`, and the `vellum` installed CLI surface | Keep the design command family in Vellum and have commands forward to the installed design-import backend and UI build tooling. |

C7 is the first implementation slice. The C2 and C14 moves remain planned until the conformance gate has a Vellum owner and test lane.
