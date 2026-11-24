# Design emitter conformance ledger

The Vellum design-emitter conformance lane owns these fixtures and generates
their baked C++ from the emitter source named below. The executable registers
Vellum-owned display-widget stand-ins for `pulp.level`, `pulp.series`, and
`pulp.bins`, so the audio-display fixtures exercise all four emitters without
linking Pulp's audio archive.

| Fixture | Input | Emitter source paths |
| --- | --- | --- |
| `spectr` | `fixtures/emitter-conformance/spectr.ir.json` | `framework/core/view/src/design_import.cpp`; `framework/core/view/src/design_codegen.cpp`; `framework/core/view/src/design_cpp_tree_codegen.cpp`; `framework/core/view/src/design_swift_codegen.cpp` |
| `kelvin` | `fixtures/emitter-conformance/kelvin.ir.json` | `framework/core/view/src/design_import.cpp`; `framework/core/view/src/design_codegen.cpp`; `framework/core/view/src/design_cpp_tree_codegen.cpp`; `framework/core/view/src/design_swift_codegen.cpp` |

The conformance implementation and generator are
`authoring/design_pipeline/tests/design_emitter_conformance_test.cpp` and
`authoring/design_pipeline/tests/emitter_conformance_gen.cpp`.
