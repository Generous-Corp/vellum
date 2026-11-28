// Builds the view that vellum-design-emit wrote for a Figma plugin export.
// The generated source is compiled into this binary at build time, so this
// test fails when the emitted pair stops compiling, stops linking against the
// framework, or builds an empty view.

#include "emitted_ui.hpp"

#include <cstdio>

int main() {
    const auto view = build_imported_ui();
    if (!view) {
        std::fprintf(stderr, "FAIL: build_imported_ui() returned no view\n");
        return 1;
    }
    if (view->child_count() == 0) {
        std::fprintf(stderr, "FAIL: the emitted view has no children\n");
        return 1;
    }
    std::printf("PASS: emitted view built with %zu top-level children\n", view->child_count());
    return 0;
}
