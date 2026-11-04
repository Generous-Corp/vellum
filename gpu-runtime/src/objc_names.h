#pragma once

// Per-binary-unique Objective-C class names for the device layer.
//
// metal_surface_mac.mm defines an ObjC class (GpuRuntimeMetalSurfaceView, the
// CAMetalLayer-backed GPU surface view). Every plug-in that links gpu-runtime
// would otherwise register the same class name, and loading two of them into
// one host makes the ObjC runtime report "Class X is implemented in both ..."
// and lets the first-loaded copy shadow the others.
//
// A shipped binary defines GPU_RUNTIME_OBJC_SUFFIX (a per-binary token) on its
// copy of the device library, and the class name gains that suffix. No-op when
// the suffix is undefined.

#ifdef GPU_RUNTIME_OBJC_SUFFIX

#define GPU_RUNTIME_OBJC_PASTE2(a, b) a##b
#define GPU_RUNTIME_OBJC_PASTE(a, b) GPU_RUNTIME_OBJC_PASTE2(a, b)
#define GPU_RUNTIME_OBJC_NAME(base) GPU_RUNTIME_OBJC_PASTE(base, GPU_RUNTIME_OBJC_SUFFIX)

#define GpuRuntimeMetalSurfaceView GPU_RUNTIME_OBJC_NAME(GpuRuntimeMetalSurfaceView)

#endif  // GPU_RUNTIME_OBJC_SUFFIX
