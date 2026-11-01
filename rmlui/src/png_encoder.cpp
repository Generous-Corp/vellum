#include <cstdint>
#include <string>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#define STB_IMAGE_WRITE_STATIC
#define STBI_WRITE_NO_STDIO
#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunused-function"
#elif defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#endif
#include <stb_image_write.h>
#if defined(__clang__)
#pragma clang diagnostic pop
#elif defined(__GNUC__)
#pragma GCC diagnostic pop
#endif

namespace vellum::rmlui {

// stb_image_write's own deflate makes the encoding a pure function of the
// pixels, so equal frames produce byte-identical files on every platform.
std::string encode_png_rgba(const std::uint8_t* straight_rgba, std::uint32_t width,
                            std::uint32_t height) {
    std::string out;
    const auto append = [](void* context, void* data, int size) {
        static_cast<std::string*>(context)->append(static_cast<const char*>(data),
                                                   static_cast<std::size_t>(size));
    };
    if (!stbi_write_png_to_func(append, &out, static_cast<int>(width), static_cast<int>(height), 4,
                                straight_rgba, static_cast<int>(width) * 4))
        out.clear();
    return out;
}

} // namespace vellum::rmlui
