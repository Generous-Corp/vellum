find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)

set(VELLUM_SWIFT_PROOF_SDK_PREFIX "" CACHE PATH
    "Installed Vellum SDK prefix that the Swift host-boundary proof builds against")
add_test(NAME vellum.swift-host-proof
    COMMAND "${Python3_EXECUTABLE}" -I
        "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_sterile_proof.py"
        --sdk-prefix "${VELLUM_SWIFT_PROOF_SDK_PREFIX}"
        --package "${CMAKE_CURRENT_SOURCE_DIR}"
        --work "${CMAKE_BINARY_DIR}/swift-host-proof")
set_tests_properties(vellum.swift-host-proof PROPERTIES
    SKIP_RETURN_CODE 77
    ENVIRONMENT "MTL_DEBUG_LAYER=0")
add_test(NAME vellum.swift-host-proof-unit
    COMMAND "${Python3_EXECUTABLE}" -I
        "${CMAKE_CURRENT_SOURCE_DIR}/scripts/test_run_sterile_proof.py")
