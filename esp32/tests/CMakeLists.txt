# Host-side tests for the Matrix Studio device protocol layer.
#
# Deliberately a plain CMake project with no ESP-IDF dependency: the parser in
# esp32/main/protocol/ is written against plain buffers and the shared contract
# header protocol/matrix_studio_protocol.h, so it compiles and runs on any host.

cmake_minimum_required(VERSION 3.16)
project(matrix_studio_esp32_host_tests CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(CheckCXXSourceRuns)

set(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../..")
set(FIRMWARE_MAIN "${CMAKE_CURRENT_SOURCE_DIR}/../main")

if(NOT EXISTS "${REPO_ROOT}/protocol/matrix_studio_protocol.h")
    message(FATAL_ERROR "Cannot find protocol/matrix_studio_protocol.h relative to ${REPO_ROOT}")
endif()

set(PROTOCOL_SOURCE "${FIRMWARE_MAIN}/protocol/ms_protocol.cpp")

add_executable(test_ms_protocol
    test_ms_protocol.cpp
    "${PROTOCOL_SOURCE}"
)

add_executable(test_ota_protocol
    test_ota_protocol.cpp
    "${PROTOCOL_SOURCE}"
)

set(MATRIX_STUDIO_TEST_TARGETS test_ms_protocol test_ota_protocol)
foreach(target IN LISTS MATRIX_STUDIO_TEST_TARGETS)
    target_include_directories(${target} PRIVATE
        "${FIRMWARE_MAIN}/protocol"
        "${REPO_ROOT}/protocol"
    )
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic)
endforeach()

# Golden fixtures are used by the primary protocol test executable.
target_compile_definitions(test_ms_protocol PRIVATE
    MATRIX_STUDIO_FIXTURE_DIR="${REPO_ROOT}/protocol/fixtures"
)

option(MATRIX_STUDIO_SANITIZE "Build host tests with ASan/UBSan when the host can run them" ON)
if(MATRIX_STUDIO_SANITIZE)
    set(SANITIZER_PROBE_SOURCE "#include <cstdlib>\nint main() { volatile int* p = (int*)malloc(sizeof(int)); *p = 1; int v = *p; free((void*)p); return v - 1; }\n")

    set(CMAKE_REQUIRED_FLAGS "-fsanitize=address,undefined")
    check_cxx_source_runs("${SANITIZER_PROBE_SOURCE}" HAVE_WORKING_ASAN_UBSAN)
    unset(CMAKE_REQUIRED_FLAGS)

    if(HAVE_WORKING_ASAN_UBSAN)
        set(MATRIX_STUDIO_SANITIZER_FLAGS -fsanitize=address,undefined)
        message(STATUS "Matrix Studio host tests: ASan + UBSan enabled")
    else()
        set(CMAKE_REQUIRED_FLAGS "-fsanitize=undefined")
        check_cxx_source_runs("${SANITIZER_PROBE_SOURCE}" HAVE_WORKING_UBSAN)
        unset(CMAKE_REQUIRED_FLAGS)
        if(HAVE_WORKING_UBSAN)
            set(MATRIX_STUDIO_SANITIZER_FLAGS -fsanitize=undefined)
            message(WARNING "Matrix Studio host tests: ASan unavailable; falling back to UBSan")
        else()
            set(MATRIX_STUDIO_SANITIZER_FLAGS "")
            message(WARNING "Matrix Studio host tests: no working sanitizer on this host")
        endif()
    endif()

    if(MATRIX_STUDIO_SANITIZER_FLAGS)
        foreach(target IN LISTS MATRIX_STUDIO_TEST_TARGETS)
            target_compile_options(${target} PRIVATE
                ${MATRIX_STUDIO_SANITIZER_FLAGS} -fno-omit-frame-pointer -fno-sanitize-recover=all)
            target_link_options(${target} PRIVATE ${MATRIX_STUDIO_SANITIZER_FLAGS})
        endforeach()
    endif()
endif()

enable_testing()
add_test(NAME ms_protocol COMMAND test_ms_protocol)
add_test(NAME ota_protocol COMMAND test_ota_protocol)
