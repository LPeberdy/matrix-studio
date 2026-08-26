# Matrix Studio ESP32-S3 firmware (ESP-IDF v5.x).
#
# Toolchain choice is deliberate — see docs/hardware.md "Library and toolchain
# choice" for why this is ESP-IDF with esphome/esp-hub75 rather than
# PlatformIO/Arduino with ESP32-HUB75-MatrixPanel-DMA.
#
#   idf.py set-target esp32s3
#   idf.py menuconfig      # "Matrix Studio" menu
#   idf.py build flash monitor
#
# Host-side protocol tests need none of this; see esp32/tests/.

cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(matrix_studio)
