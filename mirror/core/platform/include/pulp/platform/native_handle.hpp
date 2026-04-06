#pragma once

#include <cstdint>

// pulp::platform — opaque native handle types for platform windowing

namespace pulp::platform {

// Opaque handle to a native OS window
// macOS: NSWindow*    iOS: UIWindow*
// Windows: HWND       Linux: X11 Window (unsigned long)
// Cast to the appropriate type in platform-specific code
using NativeWindowHandle = void*;

// Opaque handle to a native OS view/surface
// macOS: NSView*      iOS: UIView*
// Windows: HWND       Linux: X11 Window
using NativeViewHandle = void*;

// Opaque handle to a native display/screen
// macOS: NSScreen*    Windows: HMONITOR    Linux: Display*
using NativeDisplayHandle = void*;

} // namespace pulp::platform
