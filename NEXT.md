
## C5 failing-first evidence

Added `cli/tests/test_import_backend.py::ImportBackendTests::test_standalone_jsx_modes_and_svg_control_map_validation`.
On the base branch it fails before implementation because the installed CLI rejects the new mode flags:
`unrecognized arguments: --mode live --emit js` (exit 2).
