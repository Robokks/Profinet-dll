cmake_minimum_required(VERSION 3.16)
project(VFDSimulator LANGUAGES C)

set(CMAKE_C_STANDARD 11)
add_compile_definitions(_WIN32_WINNT=0x0601 WINVER=0x0601)

set(NPCAP_SDK_DIR "${CMAKE_SOURCE_DIR}/../third_party/npcap-sdk"
    CACHE PATH "Npcap SDK root (contains Include/ and Lib/)")

add_executable(vfd_simulator WIN32
    vfd_sim.c
    pn_device.c
    ../src/frame_io_win.c
    ../src/utils_common.c
    ../src/utils_win.c
)

target_include_directories(vfd_simulator PRIVATE
    .
    ../src
    ../include
    ${NPCAP_SDK_DIR}/Include
)

target_compile_options(vfd_simulator PRIVATE
    -Wall -Wextra -Wno-unused-parameter
    -mstackrealign
)

target_link_options(vfd_simulator PRIVATE
    -static-libgcc
    -Wl,--enable-stdcall-fixup
    -Wl,-subsystem,windows
)

target_link_libraries(vfd_simulator PRIVATE
    comctl32
    comdlg32
    ws2_32
    iphlpapi
    advapi32
    winmm
)

set_target_properties(vfd_simulator PROPERTIES
    OUTPUT_NAME "vfd_simulator"
    SUFFIX ".exe"
)
