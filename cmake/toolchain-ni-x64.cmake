# NI Linux RT — Intel x86-64 (PXI-8840, PXI-8821, cRIO-9049)
# Uses the host GCC by default on an x86-64 Linux build machine.
# Override CMAKE_C_COMPILER if cross-compiling from a non-x64 host.
set(CMAKE_SYSTEM_NAME      Linux)
set(CMAKE_SYSTEM_PROCESSOR x86_64)

# Use host compiler unless explicitly overridden
if(NOT CMAKE_C_COMPILER)
    set(CMAKE_C_COMPILER   gcc)
endif()
if(NOT CMAKE_CXX_COMPILER)
    set(CMAKE_CXX_COMPILER g++)
endif()

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
