# Design emitter conformance ledger

The Vellum design-emitter conformance lane owns these fixtures and generates
their baked C++ from the emitter source named below. The executable registers
Vellum-owned display-widget stand-ins for `pulp.level`, `pulp.series`, and
`pulp.bins`, so the audio-display fixtures exercise the three emitters
(`design_codegen.cpp`, `design_cpp_tree_codegen.cpp`, `design_swift_codegen.cpp`)
and the native importer without linking Pulp's audio archive.
`design_import.cpp` is the native importer the emitters are compared against,
not an emitter.

The stand-ins hold no data. Anything the real displays do with `state.*`,
level, series, or bin values is invisible to this gate; it compares structure,
kind, paint bounds, and bindings only. The stand-in ids are the ones the
importer requests. A test fails unless the level display (`pulp.level`) is
requested and no other stand-in is, so an id drift between the importer and the
registry cannot hide behind the stand-ins. No fixture reaches `pulp.series` or
`pulp.bins`; those stand-ins are registered but unexercised, and adding a
fixture that uses one requires updating that test.

| Fixture | Input | Emitter source paths |
| --- | --- | --- |
| `spectr` | `fixtures/emitter-conformance/spectr.ir.json` | `framework/core/view/src/design_import.cpp`; `framework/core/view/src/design_codegen.cpp`; `framework/core/view/src/design_cpp_tree_codegen.cpp`; `framework/core/view/src/design_swift_codegen.cpp` |
| `kelvin` | `fixtures/emitter-conformance/kelvin.ir.json` | `framework/core/view/src/design_import.cpp`; `framework/core/view/src/design_codegen.cpp`; `framework/core/view/src/design_cpp_tree_codegen.cpp`; `framework/core/view/src/design_swift_codegen.cpp` |

The conformance implementation and generator are
`authoring/design_pipeline/tests/design_emitter_conformance_test.cpp` and
`authoring/design_pipeline/tests/emitter_conformance_gen.cpp`.
