#ifndef USB_DESCRIPTORS_H_
#define USB_DESCRIPTORS_H_

// HID instance numbers, in the order the interfaces appear in the configuration.
enum {
  HID_INST_KEYBOARD = 0,  // boot keyboard
  HID_INST_MOUSE = 1,     // boot mouse, relative moves
  HID_INST_POINTER = 2,   // absolute pointer
};

// Report ID of the absolute pointer on the pointer interface.
#define REPORT_ID_ABS_POINTER 1

// Absolute coordinates run from 0 to this value on both axes.
#define POINTER_MAX 32767

#endif
