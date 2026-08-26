# Host-side tests for the Matrix Studio device protocol layer.
#
# Deliberately a plain CMake project with no ESP-IDF dependency: the parser in
# esp32/main/protocol/ is written against plain buffers and the shared contract
# header protocol/matrix_studio_protocol.h, so it compiles and runs on any host.
#
#   cmake -B build && cmake --build build && ctest --test-dir build
#
# There is no network fetch and no external test framework — assertions are a
# handful of macros in test_ms_protocol.cpp — so this works fully offline.

cmake_minimum_required(VERSION 3.16)
project(matrix_studio_esp32_host_tests CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

include(CheckCXXSourceRuns)

set(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../..")
set(FIRMWARE_MAIN "${CMAKE_CURRENT_SOURCE_DIR}/../main")

if(NOT EXISTS "${REPO_ROOT}/protocol/matrix_studio_protocol.h")
    message(FATAL_ERROR
        "Cannot find protocol/matrix_studio_protocol.h relative to ${REPO_ROOT}. "
        "These tests must be run from inside a matrix-studio checkout.")
endif()

add_executable(test_ms_protocol
    test_ms_protocol.cpp
    "${FIRMWARE_MAIN}/protocol/ms_protocol.cpp"
)

target_include_directories(test_ms_protocol PRIVATE
    "${FIRMWARE_MAIN}/protocol"
    "${REPO_ROOT}/protocol"          # the frozen contract header, used as-is
)

# The golden fixtures are read from the repo at run time rather than embedded,
# so a regenerated fixture is picked up without touching this test.
target_compile_definitions(test_ms_protocol PRIVATE
    MATRIX_STUDIO_FIXTURE_DIR="${REPO_ROOT}/protocol/fixtures"
)

target_compile_options(test_ms_protocol PRIVATE -Wall -Wextra -Wpedantic)

# Sanitizers turn "the parser must not read past the end of a message" into an
# enforced property rather than an assertion about a return value: the
# truncated-frame fixture and the prefix sweep in test_ms_protocol.cpp parse
# out of exactly-sized heap allocations, so any over-read lands in an ASan
# redzone and aborts the run.
#
# The probe below *runs* a sanitized binary rather than merely compiling one.
# AddressSanitizer links fine but aborts at startup on hosts whose kernel has a
# narrower virtual address space than its shadow-map layout assumes (39-bit VA
# aarch64 boards such as a stock Raspberry Pi are the common case), so a
# compile-only check would wrongly enable it there. UBSan has no such
# requirement and is kept even when ASan has to be dropped.
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
            message(WARNING
                "Matrix Studio host tests: AddressSanitizer cannot run on this host "
                "(common on 39-bit-VA aarch64 kernels); falling back to UBSan only. "
                "Out-of-bounds coverage here relies on the parser's own bounds checks "
                "and on CI, which runs the same tests with ASan enabled.")
        else()
            set(MATRIX_STUDIO_SANITIZER_FLAGS "")
            message(WARNING "Matrix Studio host tests: no working sanitizer on this host, building without")
        endif()
    endif()

    if(MATRIX_STUDIO_SANITIZER_FLAGS)
        target_compile_options(test_ms_protocol PRIVATE
            ${MATRIX_STUDIO_SANITIZER_FLAGS} -fno-omit-frame-pointer -fno-sanitize-recover=all)
        target_link_options(test_ms_protocol PRIVATE ${MATRIX_STUDIO_SANITIZER_FLAGS})
    endif()
endif()

enable_testing()
add_test(NAME ms_protocol COMMAND test_ms_protocol)
