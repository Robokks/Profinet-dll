cmake_minimum_required(VERSION 3.16)
project(ProfinetDLL VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_C_STANDARD   11)
set(CMAKE_CXX_STANDARD 14)

add_compile_definitions(
    PROFINET_VERSION_MAJOR=${PROJECT_VERSION_MAJOR}
    PROFINET_VERSION_MINOR=${PROJECT_VERSION_MINOR}
    PROFINET_VERSION_PATCH=${PROJECT_VERSION_PATCH}
    PROFINET_DLL_EXPORTS
)

# ── Platform source selection ─────────────────────────────────────────────────
if(WIN32)
    add_compile_definitions(
        _WIN32_WINNT=0x0601
        WINVER=0x0601
    )

    set(NPCAP_SDK_DIR "${CMAKE_SOURCE_DIR}/third_party/npcap-sdk"
        CACHE PATH "Path to Npcap SDK directory")

    set(PLATFORM_SOURCES
        src/platform_win.c
        src/frame_io_win.c
        src/rt_cyclic_win.c
        src/utils_win.c
    )
    set(PLATFORM_INCDIRS "${NPCAP_SDK_DIR}/Include")
    set(PLATFORM_LIBS    ws2_32 iphlpapi advapi32 winmm)
    set(PLATFORM_CFLAGS  -mstackrealign -fno-exceptions)
    set(PLATFORM_LDFLAGS
        -static-libgcc
        -static-libstdc++
        -Wl,--enable-stdcall-fixup
        -Wl,--add-stdcall-alias
        -Wl,-subsystem,windows
    )
    set(LIB_SUFFIX ".dll")
    set(LIB_PREFIX "")
else()
    set(PLATFORM_SOURCES
        src/platform_linux.c
        src/frame_io_linux.c
        src/rt_cyclic_linux.c
        src/utils_linux.c
    )
    set(PLATFORM_INCDIRS "")
    set(PLATFORM_LIBS    pthread rt)
    set(PLATFORM_CFLAGS  -fPIC -fno-exceptions)
    set(PLATFORM_LDFLAGS "")
    set(LIB_SUFFIX ".so")
    set(LIB_PREFIX "lib")
endif()

# ── Shared sources (platform-agnostic) ───────────────────────────────────────
set(SHARED_SOURCES
    src/api.c
    src/pnet_state.c
    src/dcp.c
    src/rpc_cm.c
    src/profidrive.c
    src/gsdml_parser.c
    src/alarm.c
    src/utils_common.c
)

add_library(profinet SHARED ${SHARED_SOURCES} ${PLATFORM_SOURCES})

target_include_directories(profinet PRIVATE
    include
    src
    ${PLATFORM_INCDIRS}
)

target_link_libraries(profinet PRIVATE ${PLATFORM_LIBS})

target_compile_options(profinet PRIVATE
    -Wall
    -Wextra
    -Wno-unused-parameter
    ${PLATFORM_CFLAGS}
)

if(PLATFORM_LDFLAGS)
    target_link_options(profinet PRIVATE ${PLATFORM_LDFLAGS})
endif()

set_target_properties(profinet PROPERTIES
    OUTPUT_NAME "profinet"
    PREFIX      "${LIB_PREFIX}"
    SUFFIX      "${LIB_SUFFIX}"
)

# ── Install ───────────────────────────────────────────────────────────────────
install(TARGETS profinet DESTINATION bin)
install(FILES include/profinet_api.h DESTINATION include)

# ── Summary ───────────────────────────────────────────────────────────────────
message(STATUS "Build type  : ${CMAKE_BUILD_TYPE}")
message(STATUS "C compiler  : ${CMAKE_C_COMPILER}")
message(STATUS "Platform    : ${CMAKE_SYSTEM_NAME}/${CMAKE_SYSTEM_PROCESSOR}")
if(WIN32)
    message(STATUS "Npcap SDK   : ${NPCAP_SDK_DIR}")
endif()
