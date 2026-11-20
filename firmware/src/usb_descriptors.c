// USB identity of the board as seen by the target: a boot keyboard, a boot
// mouse and an absolute pointer, plus a serial port for commands in builds
// with KVM_USB_CDC=1. Without it the device is plain HID, which is what
// firmware setup screens and boot loaders handle best.
//
// The keyboard and the mouse come first and follow the HID boot protocol, the
// only one such firmware understands. The absolute pointer needs a full
// operating system.
#include <string.h>
#include "pico/unique_id.h"
#include "tusb.h"
#include "usb_descriptors.h"

// pid.codes test VID/PID: fine for development, not for distribution.
#define USB_VID 0x1209
#define USB_PID (KVM_USB_CDC ? 0x0001 : 0x0003)
#define USB_BCD 0x0200

enum {
  ITF_NUM_KEYBOARD = 0,
  ITF_NUM_MOUSE,
  ITF_NUM_POINTER,
#if KVM_USB_CDC
  ITF_NUM_CDC,
  ITF_NUM_CDC_DATA,
#endif
  ITF_NUM_TOTAL
};

#define EP_KEYBOARD_IN 0x81
#define EP_MOUSE_IN    0x82
#define EP_POINTER_IN  0x83
#define EP_CDC_NOTIF   0x84
#define EP_CDC_OUT     0x05
#define EP_CDC_IN      0x85

enum {
  STR_LANGID = 0,
  STR_MANUFACTURER,
  STR_PRODUCT,
  STR_SERIAL,
  STR_KEYBOARD,
  STR_MOUSE,
  STR_POINTER,
  STR_CDC,
};

static const tusb_desc_device_t desc_device = {
  .bLength = sizeof(tusb_desc_device_t),
  .bDescriptorType = TUSB_DESC_DEVICE,
  .bcdUSB = 0x0200,
#if KVM_USB_CDC
  // The serial port uses an Interface Association Descriptor, so the device
  // declares the "miscellaneous / common class / IAD" triple.
  .bDeviceClass = TUSB_CLASS_MISC,
  .bDeviceSubClass = MISC_SUBCLASS_COMMON,
  .bDeviceProtocol = MISC_PROTOCOL_IAD,
#endif
  .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
  .idVendor = USB_VID,
  .idProduct = USB_PID,
  .bcdDevice = USB_BCD,
  .iManufacturer = STR_MANUFACTURER,
  .iProduct = STR_PRODUCT,
  .iSerialNumber = STR_SERIAL,
  .bNumConfigurations = 1,
};

const uint8_t *tud_descriptor_device_cb(void) {
  return (const uint8_t *)&desc_device;
}

// Keyboard: the standard 8-byte boot report, no report ID, so firmware setup
// screens that only speak the boot protocol can use it.
static const uint8_t desc_hid_keyboard[] = {
  TUD_HID_REPORT_DESC_KEYBOARD()
};

// Mouse: buttons and relative X/Y in its first three bytes, as the boot
// protocol requires, then wheel and pan. No report ID.
static const uint8_t desc_hid_mouse[] = {
  TUD_HID_REPORT_DESC_MOUSE()
};

// Pointer: buttons, absolute X/Y (0..32767), wheel and horizontal pan.
static const uint8_t desc_hid_pointer[] = {
  TUD_HID_REPORT_DESC_ABSMOUSE(HID_REPORT_ID(REPORT_ID_ABS_POINTER))
};

const uint8_t *tud_hid_descriptor_report_cb(uint8_t instance) {
  switch (instance) {
    case HID_INST_KEYBOARD: return desc_hid_keyboard;
    case HID_INST_MOUSE: return desc_hid_mouse;
    default: return desc_hid_pointer;
  }
}

#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + 3 * TUD_HID_DESC_LEN + KVM_USB_CDC * TUD_CDC_DESC_LEN)

static const uint8_t desc_configuration[] = {
  TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN,
                        TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
  TUD_HID_DESCRIPTOR(ITF_NUM_KEYBOARD, STR_KEYBOARD, HID_ITF_PROTOCOL_KEYBOARD,
                     sizeof(desc_hid_keyboard), EP_KEYBOARD_IN, 8, 1),
  TUD_HID_DESCRIPTOR(ITF_NUM_MOUSE, STR_MOUSE, HID_ITF_PROTOCOL_MOUSE,
                     sizeof(desc_hid_mouse), EP_MOUSE_IN, 8, 1),
  TUD_HID_DESCRIPTOR(ITF_NUM_POINTER, STR_POINTER, HID_ITF_PROTOCOL_NONE,
                     sizeof(desc_hid_pointer), EP_POINTER_IN, 16, 1),
#if KVM_USB_CDC
  TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, STR_CDC, EP_CDC_NOTIF, 8, EP_CDC_OUT, EP_CDC_IN, 64),
#endif
};

const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
  (void)index;
  return desc_configuration;
}

static const char *const string_table[] = {
  [STR_MANUFACTURER] = "mcp-kvm",
  [STR_PRODUCT] = "mcp-kvm HID",
  [STR_KEYBOARD] = "mcp-kvm keyboard",
  [STR_MOUSE] = "mcp-kvm mouse",
  [STR_POINTER] = "mcp-kvm pointer",
  [STR_CDC] = "mcp-kvm control",
};

const uint16_t *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
  (void)langid;
  static uint16_t buf[1 + 32];
  char serial[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];
  const char *str;
  size_t len;

  if (index == STR_LANGID) {
    buf[1] = 0x0409;
    len = 1;
  } else {
    if (index == STR_SERIAL) {
      pico_get_unique_board_id_string(serial, sizeof(serial));
      str = serial;
    } else if (index < TU_ARRAY_SIZE(string_table) && string_table[index]) {
      str = string_table[index];
    } else {
      return NULL;
    }
    len = strlen(str);
    if (len > 32) len = 32;
    for (size_t i = 0; i < len; i++) buf[1 + i] = (uint8_t)str[i];
  }
  buf[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * len + 2));
  return buf;
}
