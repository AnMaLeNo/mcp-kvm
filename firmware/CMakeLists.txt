cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD waveshare_rp2040_zero CACHE STRING "Board")
include(pico_sdk_import.cmake)

project(mcp_kvm_firmware C CXX ASM)
pico_sdk_init()

# Parts shared by every firmware: the single-wire link and the recovery logic.
function(kvm_firmware name)
  target_sources(${name} PRIVATE src/wire.c src/recovery.c)
  pico_generate_pio_header(${name} ${CMAKE_CURRENT_LIST_DIR}/src/wire.pio)
  target_include_directories(${name} PRIVATE src)
  target_compile_options(${name} PRIVATE -Wall -Wextra)
  target_link_libraries(${name} PRIVATE
    pico_stdlib pico_unique_id pico_bootrom hardware_watchdog hardware_pio tinyusb_device)
  # USB is driven by our own descriptors; no SDK stdio on USB or UART.
  pico_enable_stdio_usb(${name} 0)
  pico_enable_stdio_uart(${name} 0)
  pico_add_extra_outputs(${name})
endfunction()

# Target board, plain HID: keyboard + pointer, commands over the wire.
add_executable(kvm_hid src/main.c src/usb_descriptors.c)
target_compile_definitions(kvm_hid PRIVATE KVM_USB_CDC=0)
kvm_firmware(kvm_hid)

# Target board with a serial port added: commands can also come through the
# target itself (single-board setup).
add_executable(kvm_hid_cdc src/main.c src/usb_descriptors.c)
target_compile_definitions(kvm_hid_cdc PRIVATE KVM_USB_CDC=1)
kvm_firmware(kvm_hid_cdc)

# Bridge board: USB serial port on the controlling machine <-> wire.
add_executable(kvm_bridge src/bridge/main.c src/bridge/usb_descriptors.c)
target_include_directories(kvm_bridge BEFORE PRIVATE src/bridge)
kvm_firmware(kvm_bridge)
