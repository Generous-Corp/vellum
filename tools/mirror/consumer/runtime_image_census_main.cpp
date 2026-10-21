// Enumerate the images the loader actually mapped after loading installed
// frontends through their raw/intermediate dylib paths.

#include <dlfcn.h>
#include <mach-o/dyld.h>

#include <cstddef>
#include <cstdio>
#include <cstring>

namespace {

int fail(const char* what) {
    std::fprintf(stderr, "runtime image census: %s\n", what);
    return 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        return fail(
            "usage: runtime-image-census <expected-provider> <installed-image> [...]");
    }

    for (int index = 2; index < argc; ++index) {
        void* const image = dlopen(argv[index], RTLD_NOW | RTLD_LOCAL);
        if (image == nullptr) return fail(dlerror());
    }

    std::size_t provider_count = 0;
    const char* provider_image = nullptr;
    for (uint32_t index = 0; index < _dyld_image_count(); ++index) {
        const char* const image = _dyld_get_image_name(index);
        if (image == nullptr) continue;
        if (std::strstr(image, "libvellum-") != nullptr) {
            std::printf("runtime image: %s\n", image);
        }
        if (std::strstr(image, "libvellum-skia-dawn-provider") != nullptr) {
            ++provider_count;
            provider_image = image;
        }
    }
    if (provider_count != 1) {
        std::fprintf(stderr,
                     "runtime image census: expected one provider image, found %zu\n",
                     provider_count);
        return 1;
    }
    if (std::strcmp(provider_image, argv[1]) != 0) {
        std::fprintf(stderr,
                     "runtime image census: provider image was %s, expected %s\n",
                     provider_image, argv[1]);
        return 1;
    }
    std::printf("runtime image census: one provider image: %s\n", provider_image);
    return 0;
}
