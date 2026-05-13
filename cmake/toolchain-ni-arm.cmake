# NI Linux RT — ARM Cortex-A9 (cRIO 9067, 9068, 9074, 9082)
# Requires: apt-get install gcc-arm-linux-gnueabihf g++-arm-linux-gnueabihf
set(CMAKE_SYSTEM_NAME      Linux)
set(CMAKE_SYSTEM_PROCESSOR arm)

set(CMAKE_C_COMPILER   arm-linux-gnueabihf-gcc)
set(CMAKE_CXX_COMPILER arm-linux-gnueabihf-g++)

add_compile_options(
    -march=armv7-a
    -mtune=cortex-a9
    -mfpu=neon
    -mfloat-abi=hard
)

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
