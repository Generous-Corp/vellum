# Vellum CLI

The CLI is a dependency-free Python authoring shell. Project creation, lock
validation, prerequisite diagnosis, and the installed DesignIR import/reimport
backend work today. Native build/runtime commands use the same small JSON
backend protocol and fail clearly until those SDK capabilities are installed.

Run its tests with:

```sh
python3 -m unittest discover -s cli/tests -v
```

The executable filesystem contract is documented in
[`docs/cli/import-reimport.md`](../docs/cli/import-reimport.md).
