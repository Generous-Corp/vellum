#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "VellumGpuRuntime::Bootstrap" for configuration "Release"
set_property(TARGET VellumGpuRuntime::Bootstrap APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(VellumGpuRuntime::Bootstrap PROPERTIES
  IMPORTED_LINK_INTERFACE_LANGUAGES_RELEASE "CXX"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libgpu-runtime-bootstrap.a"
  )

list(APPEND _cmake_import_check_targets VellumGpuRuntime::Bootstrap )
list(APPEND _cmake_import_check_files_for_VellumGpuRuntime::Bootstrap "${_IMPORT_PREFIX}/lib/libgpu-runtime-bootstrap.a" )

# Import target "VellumGpuRuntime::Device" for configuration "Release"
set_property(TARGET VellumGpuRuntime::Device APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(VellumGpuRuntime::Device PROPERTIES
  IMPORTED_LINK_INTERFACE_LANGUAGES_RELEASE "CXX;OBJCXX"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libgpu-runtime-device.a"
  )

list(APPEND _cmake_import_check_targets VellumGpuRuntime::Device )
list(APPEND _cmake_import_check_files_for_VellumGpuRuntime::Device "${_IMPORT_PREFIX}/lib/libgpu-runtime-device.a" )

# Import target "VellumGpuRuntime::SkiaLogBridge" for configuration "Release"
set_property(TARGET VellumGpuRuntime::SkiaLogBridge APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(VellumGpuRuntime::SkiaLogBridge PROPERTIES
  IMPORTED_LINK_INTERFACE_LANGUAGES_RELEASE "CXX"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/lib/libgpu-runtime-skia-log-bridge.a"
  )

list(APPEND _cmake_import_check_targets VellumGpuRuntime::SkiaLogBridge )
list(APPEND _cmake_import_check_files_for_VellumGpuRuntime::SkiaLogBridge "${_IMPORT_PREFIX}/lib/libgpu-runtime-skia-log-bridge.a" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
