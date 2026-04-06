#pragma once

// pulp::platform — compile-time and runtime platform detection

namespace pulp::platform {

// ── Operating System ───────────────────────────────────────────────────────

enum class OS { macOS, iOS, Windows, Linux, Android, Unknown };

#if defined(__APPLE__)
    #include <TargetConditionals.h>
    #if TARGET_OS_IPHONE
        inline constexpr OS current_os = OS::iOS;
    #else
        inline constexpr OS current_os = OS::macOS;
    #endif
    inline constexpr bool is_apple = true;
#elif defined(_WIN32)
    inline constexpr OS current_os = OS::Windows;
    inline constexpr bool is_apple = false;
#elif defined(__ANDROID__)
    inline constexpr OS current_os = OS::Android;
    inline constexpr bool is_apple = false;
#elif defined(__linux__)
    inline constexpr OS current_os = OS::Linux;
    inline constexpr bool is_apple = false;
#else
    inline constexpr OS current_os = OS::Unknown;
    inline constexpr bool is_apple = false;
#endif

inline constexpr bool is_macos   = (current_os == OS::macOS);
inline constexpr bool is_ios     = (current_os == OS::iOS);
inline constexpr bool is_windows = (current_os == OS::Windows);
inline constexpr bool is_linux   = (current_os == OS::Linux);
inline constexpr bool is_android = (current_os == OS::Android);
inline constexpr bool is_desktop = is_macos || is_windows || is_linux;
inline constexpr bool is_mobile  = is_ios || is_android;

// ── Architecture ───────────────────────────────────────────────────────────

enum class Arch { x86_64, ARM64, ARM32, x86, Unknown };

#if defined(__aarch64__) || defined(_M_ARM64)
    inline constexpr Arch current_arch = Arch::ARM64;
#elif defined(__x86_64__) || defined(_M_X64)
    inline constexpr Arch current_arch = Arch::x86_64;
#elif defined(__arm__) || defined(_M_ARM)
    inline constexpr Arch current_arch = Arch::ARM32;
#elif defined(__i386__) || defined(_M_IX86)
    inline constexpr Arch current_arch = Arch::x86;
#else
    inline constexpr Arch current_arch = Arch::Unknown;
#endif

inline constexpr bool is_arm   = (current_arch == Arch::ARM64 || current_arch == Arch::ARM32);
inline constexpr bool is_x86   = (current_arch == Arch::x86_64 || current_arch == Arch::x86);
inline constexpr bool is_64bit = (current_arch == Arch::x86_64 || current_arch == Arch::ARM64);

// ── Compiler ───────────────────────────────────────────────────────────────

enum class Compiler { Clang, GCC, MSVC, AppleClang, Unknown };

#if defined(__clang__)
    #if defined(__apple_build_version__)
        inline constexpr Compiler current_compiler = Compiler::AppleClang;
    #else
        inline constexpr Compiler current_compiler = Compiler::Clang;
    #endif
#elif defined(__GNUC__)
    inline constexpr Compiler current_compiler = Compiler::GCC;
#elif defined(_MSC_VER)
    inline constexpr Compiler current_compiler = Compiler::MSVC;
#else
    inline constexpr Compiler current_compiler = Compiler::Unknown;
#endif

// ── Endianness ─────────────────────────────────────────────────────────────

enum class Endian { Little, Big };

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    inline constexpr Endian current_endian = Endian::Big;
#else
    inline constexpr Endian current_endian = Endian::Little;
#endif

inline constexpr bool is_little_endian = (current_endian == Endian::Little);
inline constexpr bool is_big_endian    = (current_endian == Endian::Big);

// ── Debug ──────────────────────────────────────────────────────────────────

#if defined(NDEBUG)
    inline constexpr bool is_debug = false;
#else
    inline constexpr bool is_debug = true;
#endif

} // namespace pulp::platform
