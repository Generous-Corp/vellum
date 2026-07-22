# Vellum CLI

The CLI is a dependency-free Python authoring shell. Project creation, lock
validation, and prerequisite diagnosis work today. Runtime commands use a
small JSON backend protocol and fail clearly until a compatible SDK backend is
installed.

Run its tests with:

```sh
python3 -m unittest discover -s cli/tests -v
```
