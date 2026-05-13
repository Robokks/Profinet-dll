cmake_minimum_required(VERSION 3.16)
project(ProfinetTester LANGUAGES C)

set(CMAKE_C_STANDARD 11)

add_compile_definitions(_WIN32_WINNT=0x0601 WINVER=0x0601)

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
