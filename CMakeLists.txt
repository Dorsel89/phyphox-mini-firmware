cmake_minimum_required(VERSION 3.20.0)


#list(APPEND ZEPHYR_EXTRA_MODULES "${CMAKE_CURRENT_SOURCE_DIR}/drivers")

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})


list(APPEND BOARD_ROOT "${PROJECT_SOURCE_DIR}/boards")
#add_subdirectory(drivers)

project(phyfob)
target_sources(app PRIVATE src/event.c)
target_sources(app PRIVATE src/ble.c)
target_sources(app PRIVATE src/hdc.c)
target_sources(app PRIVATE src/bmp5.c)
target_sources(app PRIVATE src/bmpZephyr.c)

target_sources(app PRIVATE src/lsm6dsr.c)
target_sources(app PRIVATE src/lsm6dsr_reg.c)

target_sources(app PRIVATE src/stcc4Zephyr.c)
target_sources(app PRIVATE src/datalog.c)
target_sources(app PRIVATE src/sensirion_i2c.c)
target_sources(app PRIVATE src/stcc4_i2c.c)
target_sources(app PRIVATE src/sensirion_i2c_hal.c)
target_sources(app PRIVATE src/sensirion_common.c)


#target_sources(app PRIVATE src/bas.c)
target_sources(app PRIVATE src/main.c)
