// mcp-kvm target firmware: turns text commands into USB keyboard and pointer input.
//
// The native USB port is plugged into the target computer and shows up there
// as a keyboard and an absolute pointer. Commands arrive as text lines, each
// answered by exactly one line starting with "ok" or "err" (docs/protocol.md),
// over either of two channels:
//
// - the single-wire serial link to the bridge board (always on);
// - a USB serial port on the target itself, in builds with KVM_USB_CDC=1.
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pico/stdlib.h"
#include "tusb.h"
#include "recovery.h"
#include "usb_descriptors.h"
#include "wire.h"

#define FW_VERSION "0.5.0"

#ifndef KVM_WIRE_PIN
#define KVM_WIRE_PIN 2
#endif
#ifndef KVM_WIRE_BAUD
#define KVM_WIRE_BAUD 115200
#endif

static uint32_t now_ms(void) {
  return to_ms_since_boot(get_absolute_time());
}

// ---------------------------------------------------------------------------
// Report queues. Every command appends whole reports; the sender drains them
// in order, one per USB poll, so no press or release is ever merged away.
// ---------------------------------------------------------------------------
typedef struct {
  uint8_t mods;
  uint8_t keys[6];
} kbd_report_t;

typedef struct {
  uint8_t buttons;
  int16_t x, y;
  int8_t wheel, pan;
} ptr_report_t;

typedef struct {
  uint8_t buttons;
  int8_t dx, dy;
} mouse_report_t;

#define KBD_QUEUE_LEN 512 // powers of two
#define PTR_QUEUE_LEN 128
#define MOUSE_QUEUE_LEN 256

static kbd_report_t kbd_queue[KBD_QUEUE_LEN];
static uint16_t kbd_head, kbd_tail;
static ptr_report_t ptr_queue[PTR_QUEUE_LEN];
static uint16_t ptr_head, ptr_tail;

static mouse_report_t mouse_queue[MOUSE_QUEUE_LEN];
static uint16_t mouse_head, mouse_tail;
static uint8_t mouse_buttons;        // buttons held on the relative mouse

// State after everything queued so far has been sent.
static kbd_report_t kbd_cur;
static ptr_report_t ptr_cur = {.x = POINTER_MAX / 2, .y = POINTER_MAX / 2};

static uint32_t kbd_interval_ms = 6; // minimum spacing between keyboard reports
static uint32_t kbd_next_ms;
static uint8_t kbd_leds;             // LED state last set by the target
static uint32_t mount_count;         // times the target configured the device
static uint32_t last_sent_ms;        // when the target last took a report

static uint16_t kbd_count(void) { return (uint16_t)(kbd_head - kbd_tail) & (KBD_QUEUE_LEN - 1); }
static uint16_t kbd_free(void) { return KBD_QUEUE_LEN - 1 - kbd_count(); }
static uint16_t ptr_count(void) { return (uint16_t)(ptr_head - ptr_tail) & (PTR_QUEUE_LEN - 1); }
static uint16_t ptr_free(void) { return PTR_QUEUE_LEN - 1 - ptr_count(); }

static uint16_t mouse_count(void) { return (uint16_t)(mouse_head - mouse_tail) & (MOUSE_QUEUE_LEN - 1); }
static uint16_t mouse_free(void) { return MOUSE_QUEUE_LEN - 1 - mouse_count(); }

static void mouse_push(uint8_t buttons, int8_t dx, int8_t dy) {
  mouse_queue[mouse_head] = (mouse_report_t){buttons, dx, dy};
  mouse_head = (mouse_head + 1) & (MOUSE_QUEUE_LEN - 1);
  mouse_buttons = buttons;
}

static void kbd_push(const kbd_report_t *r) {
  kbd_queue[kbd_head] = *r;
  kbd_head = (kbd_head + 1) & (KBD_QUEUE_LEN - 1);
  kbd_cur = *r;
}

static void ptr_push(const ptr_report_t *r) {
  ptr_queue[ptr_head] = *r;
  ptr_head = (ptr_head + 1) & (PTR_QUEUE_LEN - 1);
  ptr_cur = *r;
  ptr_cur.wheel = 0; // wheel and pan are relative: they apply once
  ptr_cur.pan = 0;
}

static void queues_clear(void) {
  kbd_head = kbd_tail = 0;
  ptr_head = ptr_tail = 0;
  mouse_head = mouse_tail = 0;
  memset(&kbd_cur, 0, sizeof(kbd_cur));
  ptr_cur.buttons = 0;
  mouse_buttons = 0;
}

static bool queues_idle(void) {
  return kbd_count() == 0 && ptr_count() == 0 && mouse_count() == 0 &&
         tud_hid_n_ready(HID_INST_KEYBOARD) && tud_hid_n_ready(HID_INST_POINTER) &&
         tud_hid_n_ready(HID_INST_MOUSE);
}

static void reports_task(void) {
  if (!tud_mounted()) return;
  if (tud_suspended()) {
    // Input for a sleeping target: ask it to wake up (works only if it allowed that).
    if (kbd_count() || ptr_count() || mouse_count()) tud_remote_wakeup();
    return;
  }
  if (kbd_count() && now_ms() >= kbd_next_ms && tud_hid_n_ready(HID_INST_KEYBOARD)) {
    kbd_report_t *r = &kbd_queue[kbd_tail];
    if (tud_hid_n_keyboard_report(HID_INST_KEYBOARD, 0, r->mods, r->keys)) {
      kbd_tail = (kbd_tail + 1) & (KBD_QUEUE_LEN - 1);
      kbd_next_ms = now_ms() + kbd_interval_ms;
      last_sent_ms = now_ms();
    }
  }
  if (ptr_count() && tud_hid_n_ready(HID_INST_POINTER)) {
    ptr_report_t *r = &ptr_queue[ptr_tail];
    if (tud_hid_n_abs_mouse_report(HID_INST_POINTER, REPORT_ID_ABS_POINTER, r->buttons,
                                   r->x, r->y, r->wheel, r->pan)) {
      ptr_tail = (ptr_tail + 1) & (PTR_QUEUE_LEN - 1);
      last_sent_ms = now_ms();
    }
  }
  if (mouse_count() && tud_hid_n_ready(HID_INST_MOUSE)) {
    mouse_report_t *r = &mouse_queue[mouse_tail];
    // In boot protocol the report is exactly buttons, X, Y.
    uint8_t report[5] = {r->buttons, (uint8_t)r->dx, (uint8_t)r->dy, 0, 0};
    uint16_t len = tud_hid_n_get_protocol(HID_INST_MOUSE) == HID_PROTOCOL_BOOT ? 3 : 5;
    if (tud_hid_n_report(HID_INST_MOUSE, 0, report, len)) {
      mouse_tail = (mouse_tail + 1) & (MOUSE_QUEUE_LEN - 1);
      last_sent_ms = now_ms();
    }
  }
}

// ---------------------------------------------------------------------------
// Command channel
// ---------------------------------------------------------------------------
#define LINE_MAX 700
// "sync" gives up when the target has taken no report for this long. The
// limit is on silence, not on the total: a boot loader may take a key every
// 130 ms and still be making progress.
#define SYNC_STALL_MS 5000

typedef struct {
  void (*write)(const char *data, uint32_t len);
  char line[LINE_MAX];
  uint16_t len;
  bool overflow;
} channel_t;

static void wire_out(const char *data, uint32_t len) {
  wire_write(data, len);
}
static channel_t wire_channel = {.write = wire_out};

#if KVM_USB_CDC
static void cdc_out(const char *data, uint32_t len) {
  tud_cdc_write(data, len);
  tud_cdc_write_flush();
}
static channel_t cdc_channel = {.write = cdc_out};
#endif

static channel_t *reply_to;      // channel of the command being handled
static channel_t *sync_channel;  // channel waiting for the answer to "sync"

static void reply(const char *fmt, ...) {
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf) - 1, fmt, ap);
  va_end(ap);
  if (n < 0) return;
  if (n > (int)sizeof(buf) - 2) n = sizeof(buf) - 2;
  buf[n++] = '\n';
  reply_to->write(buf, (uint32_t)n);
}

// Parses a number in the given base; false if the token is missing or malformed.
static bool parse_num(const char *tok, int base, long lo, long hi, long *out) {
  if (!tok || !*tok) return false;
  char *end;
  long v = strtol(tok, &end, base);
  if (*end || v < lo || v > hi) return false;
  *out = v;
  return true;
}

static void cmd_keys(void) {
  // k [mods [key ...]] : set the whole keyboard state, hex bytes.
  kbd_report_t r = {0};
  long v;
  char *tok = strtok(NULL, " ");
  if (tok) {
    if (!parse_num(tok, 16, 0, 255, &v)) { reply("err bad mods"); return; }
    r.mods = (uint8_t)v;
    for (int i = 0; (tok = strtok(NULL, " ")) != NULL; i++) {
      if (i >= 6) { reply("err too many keys"); return; }
      if (!parse_num(tok, 16, 0, 255, &v)) { reply("err bad key"); return; }
      r.keys[i] = (uint8_t)v;
    }
  }
  if (kbd_free() < 1) { reply("err full"); return; }
  kbd_push(&r);
  reply("ok");
}

static void cmd_type(void) {
  // t mmkk [mmkk ...] : type keystrokes one after the other. Each is a
  // modifier byte and a key usage, four hex digits.
  char *toks[160];
  int n = 0;
  char *tok;
  while ((tok = strtok(NULL, " ")) != NULL) {
    if (n >= (int)TU_ARRAY_SIZE(toks)) { reply("err too many strokes"); return; }
    toks[n++] = tok;
  }
  if (n == 0) { reply("err no strokes"); return; }
  if (kbd_free() < 3 * n + 1) { reply("err full"); return; }

  for (int i = 0; i < n; i++) {
    long v;
    if (strlen(toks[i]) != 4 || !parse_num(toks[i], 16, 0, 0xffff, &v)) {
      reply("err bad stroke %d", i);
      return;
    }
  }
  for (int i = 0; i < n; i++) {
    long v = strtol(toks[i], NULL, 16);
    kbd_report_t r = {.mods = (uint8_t)(v >> 8)};
    // Modifiers go down before the key and stay down across strokes that
    // share them, as a person holding Shift would do.
    if (kbd_cur.mods != r.mods || kbd_cur.keys[0]) kbd_push(&r);
    if (v & 0xff) {
      r.keys[0] = (uint8_t)(v & 0xff);
      kbd_push(&r);
      r.keys[0] = 0;
      kbd_push(&r);
    }
  }
  if (kbd_cur.mods) {
    kbd_report_t none = {0};
    kbd_push(&none);
  }
  reply("ok");
}

static void cmd_pointer(void) {
  // p buttons x y : buttons in hex, coordinates 0..32767 in decimal.
  long b, x, y;
  if (!parse_num(strtok(NULL, " "), 16, 0, 0x1f, &b) ||
      !parse_num(strtok(NULL, " "), 10, 0, POINTER_MAX, &x) ||
      !parse_num(strtok(NULL, " "), 10, 0, POINTER_MAX, &y)) {
    reply("err usage: p buttons x y");
    return;
  }
  if (ptr_free() < 1) { reply("err full"); return; }
  ptr_report_t r = {.buttons = (uint8_t)b, .x = (int16_t)x, .y = (int16_t)y};
  ptr_push(&r);
  reply("ok");
}

static void cmd_mouse(void) {
  // m buttons dx dy : relative mouse. Buttons in hex, moves -127..127 in decimal.
  long b, dx, dy;
  if (!parse_num(strtok(NULL, " "), 16, 0, 0x07, &b) ||
      !parse_num(strtok(NULL, " "), 10, -127, 127, &dx) ||
      !parse_num(strtok(NULL, " "), 10, -127, 127, &dy)) {
    reply("err usage: m buttons dx dy");
    return;
  }
  if (mouse_free() < 1) { reply("err full"); return; }
  mouse_push((uint8_t)b, (int8_t)dx, (int8_t)dy);
  reply("ok");
}

static void cmd_wheel(void) {
  // w dy [dx] : wheel notches, positive dy scrolls up, positive dx right.
  long dy, dx = 0;
  if (!parse_num(strtok(NULL, " "), 10, -127, 127, &dy)) { reply("err usage: w dy [dx]"); return; }
  char *tok = strtok(NULL, " ");
  if (tok && !parse_num(tok, 10, -127, 127, &dx)) { reply("err bad dx"); return; }
  if (ptr_free() < 1) { reply("err full"); return; }
  ptr_report_t r = ptr_cur;
  r.wheel = (int8_t)dy;
  r.pan = (int8_t)dx;
  ptr_push(&r);
  reply("ok");
}

static void cmd_release(void) {
  if (kbd_free() < 1 || ptr_free() < 1 || mouse_free() < 1) { reply("err full"); return; }
  kbd_report_t k = {0};
  kbd_push(&k);
  mouse_push(0, 0, 0);
  ptr_report_t p = ptr_cur;
  p.buttons = 0;
  ptr_push(&p);
  reply("ok");
}

static void cmd_cfg(void) {
  char *name = strtok(NULL, " ");
  long v;
  if (name && !strcmp(name, "kms") && parse_num(strtok(NULL, " "), 10, 1, 1000, &v)) {
    kbd_interval_ms = (uint32_t)v;
    reply("ok");
  } else {
    reply("err usage: cfg kms 1..1000");
  }
}

static void cmd_gpio(void) {
  // gpio <n> : level of a pin read as an input with pull-up, to check wiring.
  long pin;
  if (!parse_num(strtok(NULL, " "), 10, 0, 29, &pin)) { reply("err usage: gpio 0..29"); return; }
  gpio_init((uint)pin);
  gpio_set_dir((uint)pin, GPIO_IN);
  gpio_pull_up((uint)pin);
  sleep_us(50);
  reply("ok %d", gpio_get((uint)pin));
}

static void handle_line(char *text) {
  char *cmd = strtok(text, " ");
  if (!cmd) return;
  bool known = true;

  if (!strcmp(cmd, "ping")) {
    reply("ok mcp-kvm %s", FW_VERSION);
  } else if (!strcmp(cmd, "echo")) {
    char *text = strtok(NULL, "");
    reply("ok %s", text ? text : "");
  } else if (!strcmp(cmd, "info")) {
    reply("ok ver=%s cdc=%d mounted=%d suspended=%d enum=%lu boot=%d%d leds=%02x kq=%u pq=%u mq=%u kms=%lu up=%lu hang=%d soft=%d failsafe=%d",
          FW_VERSION, KVM_USB_CDC, tud_mounted(), tud_suspended(), (unsigned long)mount_count,
          tud_hid_n_get_protocol(HID_INST_KEYBOARD) == HID_PROTOCOL_BOOT,
          tud_hid_n_get_protocol(HID_INST_MOUSE) == HID_PROTOCOL_BOOT,
          kbd_leds, kbd_count(), ptr_count(),
          mouse_count(), (unsigned long)kbd_interval_ms, (unsigned long)now_ms(), recovery_reset_by_hang(),
          recovery_reset_soft(), recovery_failsafe_armed());
  } else if (!strcmp(cmd, "k")) {
    cmd_keys();
  } else if (!strcmp(cmd, "t")) {
    cmd_type();
  } else if (!strcmp(cmd, "p")) {
    cmd_pointer();
  } else if (!strcmp(cmd, "m")) {
    cmd_mouse();
  } else if (!strcmp(cmd, "w")) {
    cmd_wheel();
  } else if (!strcmp(cmd, "r")) {
    cmd_release();
  } else if (!strcmp(cmd, "sync")) {
    sync_channel = reply_to; // answered by command_task once everything is sent
    last_sent_ms = now_ms();
  } else if (!strcmp(cmd, "cfg")) {
    cmd_cfg();
  } else if (!strcmp(cmd, "gpio")) {
    cmd_gpio();
  } else if (!strcmp(cmd, "wiretest")) {
    // The test writes on the wire itself: it cannot be asked for over the wire.
    if (reply_to == &wire_channel) {
      reply("err wiretest runs from the USB serial port only");
    } else {
      reply("ok %d/32 level=%d", wire_selftest(), wire_level());
    }
  } else if (!strcmp(cmd, "bootsel")) {
    reply("ok");
    recovery_bootsel_soon();
  } else if (!strcmp(cmd, "reboot")) {
    reply("ok");
    recovery_reboot_soon();
  } else {
    known = false;
    reply("err unknown command");
  }
  if (known) recovery_command_seen();
}

static void feed(channel_t *ch, int c) {
  if (c == '\n' || c == '\r') {
    reply_to = ch;
    if (ch->overflow) {
      reply("err line too long");
    } else if (ch->len) {
      ch->line[ch->len] = 0;
      handle_line(ch->line);
    }
    ch->len = 0;
    ch->overflow = false;
  } else if (ch->len < LINE_MAX - 1) {
    ch->line[ch->len++] = (char)c;
  } else {
    ch->overflow = true;
  }
}

static void command_task(void) {
  if (sync_channel) {
    reply_to = sync_channel;
    if (queues_idle()) {
      sync_channel = NULL;
      reply("ok");
    } else if (now_ms() - last_sent_ms > SYNC_STALL_MS) {
      sync_channel = NULL;
      // Drop what is waiting, then let go of everything: the last report the
      // target took may have a key or a button down, and it would repeat.
      queues_clear();
      kbd_report_t no_keys = {0};
      kbd_push(&no_keys);
      mouse_push(0, 0, 0);
      ptr_report_t no_buttons = ptr_cur;
      ptr_push(&no_buttons);
      reply("err timeout");
    }
    return; // commands behind a sync wait their turn
  }
  int c;
  while (!sync_channel && (c = wire_read()) >= 0) feed(&wire_channel, c);
#if KVM_USB_CDC
  while (!sync_channel && tud_cdc_available() && (c = tud_cdc_read_char()) >= 0) feed(&cdc_channel, c);
#endif
}

// ---------------------------------------------------------------------------
// TinyUSB callbacks
// ---------------------------------------------------------------------------
void tud_mount_cb(void) {
  mount_count++;
}

void tud_umount_cb(void) {
  queues_clear();
}

#if KVM_USB_CDC
// Opening the serial port at 1200 baud enters BOOTSEL without the parser.
void tud_cdc_line_coding_cb(uint8_t itf, cdc_line_coding_t const *coding) {
  (void)itf;
  if (coding->bit_rate == 1200) recovery_bootsel_soon();
}
#endif

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                               uint8_t *buffer, uint16_t reqlen) {
  (void)instance; (void)report_id; (void)report_type; (void)buffer; (void)reqlen;
  return 0;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                           uint8_t const *buffer, uint16_t bufsize) {
  (void)report_id;
  if (instance == HID_INST_KEYBOARD && report_type == HID_REPORT_TYPE_OUTPUT && bufsize >= 1) {
    kbd_leds = buffer[0]; // bit 0 Num Lock, bit 1 Caps Lock, bit 2 Scroll Lock
  }
}

int main(void) {
  recovery_init();
  tud_init(0);
  wire_init(KVM_WIRE_PIN, KVM_WIRE_BAUD);
  while (true) {
    recovery_task();
    tud_task();
    wire_task();
    command_task();
    reports_task();
  }
}
