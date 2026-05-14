cmake_minimum_required(VERSION 3.16)
project(ProfinetTester LANGUAGES C)

set(CMAKE_C_STANDARD 11)

add_compile_definitions(_WIN32_WINNT=0x0601 WINVER=0x0601)

set(NPCAP_SDK_DIR "${CMAKE_SOURCE_DIR}/../third_party/npcap-sdk"
    CACHE PATH "Npcap SDK root")

# GUI tester
add_executable(profinet_tester WIN32 profinet_tester.c)

target_compile_options(profinet_tester PRIVATE
    -Wall -Wno-unused-parameter -mstackrealign
)
target_link_options(profinet_tester PRIVATE
    -static-libgcc
    -Wl,-subsystem,windows
)
target_link_libraries(profinet_tester PRIVATE
    comctl32
    comdlg32
)

set_target_properties(profinet_tester PROPERTIES
    OUTPUT_NAME "profinet_tester"
    SUFFIX      ".exe"
)

# CLI integration test (talks to vfd_simulator via Npcap)
add_executable(sim_test
    sim_test.c
    ../src/frame_io_win.c
    ../src/utils_common.c
    ../src/utils_win.c
)

target_include_directories(sim_test PRIVATE
    ../src
    ../include
    ${NPCAP_SDK_DIR}/Include
)

target_compile_options(sim_test PRIVATE
    -Wall -Wno-unused-parameter -mstackrealign
)

target_link_options(sim_test PRIVATE
    -static-libgcc
    -Wl,--enable-stdcall-fixup
    -Wl,-subsystem,console
)

target_link_libraries(sim_test PRIVATE
    ws2_32
    iphlpapi
    advapi32
    winmm
)

set_target_properties(sim_test PROPERTIES
    OUTPUT_NAME "sim_test"
    SUFFIX      ".exe"
)
