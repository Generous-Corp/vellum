if(NOT DEFINED CMAKE_CXX_COMPILER OR NOT DEFINED CMAKE_OSX_SYSROOT OR NOT DEFINED SOURCE)
    message(FATAL_ERROR "the iOS Cocoa negative control needs compiler, SDK and source")
endif()

execute_process(
    COMMAND "${CMAKE_CXX_COMPILER}" -x objective-c++ -fsyntax-only
        -isysroot "${CMAKE_OSX_SYSROOT}" "${SOURCE}"
    RESULT_VARIABLE result
    OUTPUT_VARIABLE stdout
    ERROR_VARIABLE stderr)
if(result EQUAL 0)
    message(FATAL_ERROR "Cocoa unexpectedly compiled for iOS\n${stdout}\n${stderr}")
endif()
message(STATUS "Cocoa is unavailable in the iOS SDK as expected")
