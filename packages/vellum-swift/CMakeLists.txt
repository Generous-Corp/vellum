find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)

set(VELLUM_SWIFT_PROOF_SDK_PREFIX "" CACHE PATH
    "Installed Vellum SDK prefix for the Swift host proof; empty installs this build tree")
option(VELLUM_SWIFT_PROOF_REQUIRE_PINNED_TOOLCHAIN
    "Fail the Swift host proof, instead of skipping it, on other than the pinned Xcode and Swift"
    OFF)

set(_vellum_swift_proof_work "${CMAKE_BINARY_DIR}/swift-host-proof")
set(_vellum_swift_proof_prefix "${VELLUM_SWIFT_PROOF_SDK_PREFIX}")
set(_vellum_swift_proof_args)
if(_vellum_swift_proof_prefix STREQUAL "" AND TARGET vellum-host-c)
    set(_vellum_swift_proof_prefix "${_vellum_swift_proof_work}/prefix")
    add_test(NAME vellum.swift-host-proof-install
        COMMAND "${CMAKE_COMMAND}"
            "-DVELLUM_BUILD_DIR=${CMAKE_BINARY_DIR}"
            "-DVELLUM_SWIFT_PROOF_PREFIX=${_vellum_swift_proof_prefix}"
            "-DTEST_CONFIG=$<CONFIG>"
            -P "${CMAKE_CURRENT_SOURCE_DIR}/InstallSdkPrefix.cmake")
    set_tests_properties(vellum.swift-host-proof-install PROPERTIES
        FIXTURES_SETUP vellum-swift-host-sdk)
endif()
if(VELLUM_REQUIRE_GPU)
    list(APPEND _vellum_swift_proof_args --require-prefix)
endif()
if(VELLUM_SWIFT_PROOF_REQUIRE_PINNED_TOOLCHAIN)
    list(APPEND _vellum_swift_proof_args --require-pinned-toolchain)
endif()

add_test(NAME vellum.swift-host-proof
    COMMAND "${Python3_EXECUTABLE}" -I
        "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_sterile_proof.py"
        --sdk-prefix "${_vellum_swift_proof_prefix}"
        --package "${CMAKE_CURRENT_SOURCE_DIR}"
        --work "${_vellum_swift_proof_work}/run"
        ${_vellum_swift_proof_args})
set_tests_properties(vellum.swift-host-proof PROPERTIES
    SKIP_RETURN_CODE 77
    ENVIRONMENT "MTL_DEBUG_LAYER=0")
if(TEST vellum.swift-host-proof-install)
    set_tests_properties(vellum.swift-host-proof PROPERTIES
        FIXTURES_REQUIRED vellum-swift-host-sdk)
endif()

add_test(NAME vellum.swift-host-proof-unit
    COMMAND "${Python3_EXECUTABLE}" -I
        "${CMAKE_CURRENT_SOURCE_DIR}/scripts/test_run_sterile_proof.py")
