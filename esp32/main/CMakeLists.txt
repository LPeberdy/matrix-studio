idf_component_register(
    SRCS
        "app_main.cpp"
        "board_config.cpp"
        "psram_info.cpp"
        "serial_console.cpp"
        "display/display.cpp"
        "display/diagnostics.cpp"
        "display/frame_queue.cpp"
        "display/render_task.cpp"
        "net/ms_client.cpp"
        "net/wifi_station.cpp"
        "protocol/ms_protocol.cpp"
    INCLUDE_DIRS
        "."
        "display"
        "net"
        "protocol"
    REQUIRES
        driver
        esp_event
        esp_netif
        esp_timer
        esp_wifi
        nvs_flash
    PRIV_REQUIRES
        esp_app_format
        esp_hw_support
        esp_psram
        spi_flash
)

# The frozen wire contract is consumed from protocol/ at the repo root rather
# than copied in, so the firmware and the Python reference implementation can
# never drift apart silently. esp32/tests/CMakeLists.txt points at the same
# file. Do not vendor a copy here.
target_include_directories(${COMPONENT_LIB} PUBLIC "${CMAKE_CURRENT_LIST_DIR}/../../protocol")

target_compile_options(${COMPONENT_LIB} PRIVATE -Wall -Wextra -Wno-unused-parameter)
