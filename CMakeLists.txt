cmake_minimum_required(VERSION 3.16)
project(ProfinetDLL VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_C_STANDARD   11)
set(CMAKE_CXX_STANDARD 14)

# Version constants available in source code
add_compile_definitions(
    PROFINET_VERSION_MAJOR=${PROJECT_VERSION_MAJOR}
    PROFINET_VERSION_MINOR=${PROJECT_VERSION_MINOR}
    PROFINET_VERSION_PATCH=${PROJECT_VERSION_PATCH}
    PROFINET_DLL_EXPORTS
    _WIN32_WINNT=0x0601   # Windows 7 minimum
    WINVER=0x0601
)

# Npcap SDK — only headers needed at build time (DLL loaded at runtime)
set(NPCAP_SDK_DIR "${CMAKE_SOURCE_DIR}/third_party/npcap-sdk"
    CACHE PATH "Path to Npcap SDK directory")

# ── DLL source files ──────────────────────────────────────────────────────────
set(PROFINET_SOURCES
    src/profinet_dll.c
    src/api.c
    src/pnet_state.c
    src/frame_io.c
    src/dcp.c
    src/rpc_cm.c
    src/rt_cyclic.c
    src/profidrive.c
    src/gsdml_parser.c
    src/alarm.c
    src/utils.c
)

add_library(profinet SHARED ${PROFINET_SOURCES})

target_include_directories(profinet PRIVATE
    include
    src
    "${NPCAP_SDK_DIR}/Include"
)

# Windows system libs (all available in MinGW sysroot)
target_link_libraries(profinet PRIVATE
    ws2_32      # Winsock
    iphlpapi    # GetAdaptersInfo
    advapi32    # CryptGenRandom
    winmm       # timeBeginPeriod
    # wpcap.dll is loaded at RUNTIME via LoadLibrary — not linked here
)

target_compile_options(profinet PRIVATE
    -Wall
    -Wextra
    -Wno-unused-parameter
    -mstackrealign       # correct __stdcall stack for 32-bit
    -fno-exceptions
)

target_link_options(profinet PRIVATE
    -static-libgcc
    -static-libstdc++
    -Wl,--enable-stdcall-fixup
    -Wl,-subsystem,windows
)

set_target_properties(profinet PROPERTIES
    OUTPUT_NAME "profinet"
    PREFIX      ""
    SUFFIX      ".dll"
)

# ── Install ───────────────────────────────────────────────────────────────────
install(TARGETS profinet DESTINATION bin)
install(FILES include/profinet_api.h DESTINATION include)

# ── Summary ───────────────────────────────────────────────────────────────────
message(STATUS "Build type : ${CMAKE_BUILD_TYPE}")
message(STATUS "C compiler : ${CMAKE_C_COMPILER}")
message(STATUS "Npcap SDK  : ${NPCAP_SDK_DIR}")
