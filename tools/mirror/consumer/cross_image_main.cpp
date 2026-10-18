// Load two distinct provider images in one process for the provider census
// negative control. A host carrying its own Skia/Dawn provider must be
// rejected by the image census even when CMake target transitivity is hidden.

#include <dlfcn.h>
#include <mach-o/dyld.h>

#include <cstdio>
#include <cstring>

namespace {

int fail(const char* what) {
    std::fprintf(stderr, "cross-image provider census: %s\n", what);
    return 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) return fail("usage: cross-image-consumer <provider> <duplicate>");

    void* const provider = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (provider == nullptr) return fail(dlerror());
    void* const duplicate = dlopen(argv[2], RTLD_NOW | RTLD_LOCAL);
    if (duplicate == nullptr) return fail(dlerror());
    if (provider == duplicate) return fail("loader coalesced the two provider paths");

    bool saw_provider = false;
    bool saw_duplicate = false;
    for (uint32_t i = 0; i < _dyld_image_count(); ++i) {
        const char* const image = _dyld_get_image_name(i);
        if (image == nullptr) continue;
        saw_provider |= std::strstr(image, "libvellum-skia-dawn-provider.dylib") != nullptr;
        saw_duplicate |=
            std::strstr(image, "libvellum-skia-dawn-provider-duplicate.dylib") != nullptr;
    }
    if (!saw_provider || !saw_duplicate)
        return fail("two distinct provider images were not present in the process");
    std::puts("cross-image provider census: two distinct providers loaded");
    return 0;
}
