
## C5 failing-first evidence

Added `cli/tests/test_import_backend.py::ImportBackendTests::test_standalone_jsx_modes_and_svg_control_map_validation`.
On the base branch it fails before implementation because the installed CLI rejects the new mode flags:
`unrecognized arguments: --mode live --emit js` (exit 2).

After implementation commit `bfd16cdb08cbc679603f0c542758b43dd04db3ac`, temporarily restoring
`cli/vellum_cli.py` from its parent reproduced the same failure. The implementation was restored
and the focused test passes.
