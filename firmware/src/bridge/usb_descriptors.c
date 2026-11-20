// USB identity of the bridge board: one serial port.
#include <string.h>
#include "pico/unique_id.h"
#include "tusb.h"

// pid.codes test VID/PID: fine for development, not for distribution.
#define USB_VID 0x1209
#define USB_PID 0x0002

enum { ITF_NUM_CDC = 0, ITF_NUM_CDC_DATA, ITF_NUM_TOTAL };
enum { STR_LANGID = 0, STR_MANUFACTURER, STR_PRODUCT, STR_SERIAL };

static const tusb_desc_device_t desc_device = {
  .bLength = sizeof(tusb_desc_device_t),
  .bDescriptorType = TUSB_DESC_DEVICE,
  .bcdUSB = 0x0200,
  .bDeviceClass = TUSB_CLASS_MISC,
  .bDeviceSubClass = MISC_SUBCLASS_COMMON,
  .bDeviceProtocol = MISC_PROTOCOL_IAD,
  .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
  .idVendor = USB_VID,
  .idProduct = USB_PID,
  .bcdDevice = 0x0100,
  .iManufacturer = STR_MANUFACTURER,
  .iProduct = STR_PRODUCT,
  .iSerialNumber = STR_SERIAL,
  .bNumConfigurations = 1,
};

const uint8_t *tud_descriptor_device_cb(void) {
  return (const uint8_t *)&desc_device;
}

static const uint8_t desc_configuration[] = {
  TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN, 0, 100),
  TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 0, 0x81, 8, 0x02, 0x82, 64),
};

const uint8_t *tud_descriptor_configuration_cb(uint8_t index) {
  (void)index;
  return desc_configuration;
}

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
    if (index == STR_MANUFACTURER) {
      str = "mcp-kvm";
    } else if (index == STR_PRODUCT) {
      str = "mcp-kvm bridge";
    } else if (index == STR_SERIAL) {
      pico_get_unique_board_id_string(serial, sizeof(serial));
      str = serial;
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
