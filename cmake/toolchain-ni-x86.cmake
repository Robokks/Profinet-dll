# NI Linux RT — Intel x86 32-bit (cRIO 9030, 9031, 9033, 9034, 9035, 9038, 9039)
# Requires: apt-get install gcc-i686-linux-gnu g++-i686-linux-gnu
set(CMAKE_SYSTEM_NAME      Linux)
set(CMAKE_SYSTEM_PROCESSOR i686)

set(CMAKE_C_COMPILER   i686-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER i686-linux-gnu-g++)

add_compile_options(-m32)
add_link_options(-m32)

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
