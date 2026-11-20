#ifndef _TUSB_CONFIG_H_
#define _TUSB_CONFIG_H_

#define CFG_TUSB_OS                 OPT_OS_PICO
#define CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_ALIGN          __attribute__ ((aligned(4)))

// Native USB controller (rhport 0), device mode: this is the port the target sees.
#define CFG_TUD_ENABLED             1
#define CFG_TUD_ENDPOINT0_SIZE      64

// Three HID interfaces: boot keyboard, boot mouse, absolute pointer.
#define CFG_TUD_HID                 3
#define CFG_TUD_HID_EP_BUFSIZE      16

// Optional CDC port: command channel through the target itself.
#define CFG_TUD_CDC                 KVM_USB_CDC
#define CFG_TUD_CDC_RX_BUFSIZE      1024
#define CFG_TUD_CDC_TX_BUFSIZE      1024
#define CFG_TUD_CDC_EP_BUFSIZE      64

#endif
