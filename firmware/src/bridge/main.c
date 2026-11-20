// mcp-kvm bridge firmware: joins the controlling machine to the target board.
//
// The native USB port is plugged into the controlling machine and shows up
// there as a serial port. Each text line received on it is passed to the
// target board over the single-wire link, and what the target board answers
// comes back on the serial port.
//
// Lines starting with "!" are for the bridge itself (docs/protocol.md).
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "pico/stdlib.h"
#include "tusb.h"
#include "recovery.h"
#include "wire.h"

#define FW_VERSION "0.2.0"

#ifndef KVM_WIRE_PIN
#define KVM_WIRE_PIN 13
#endif
#ifndef KVM_WIRE_BAUD
#define KVM_WIRE_BAUD 115200
#endif

#define LINE_MAX 700

static char line[LINE_MAX];
static uint16_t line_len;
static bool line_overflow;

static void reply(const char *fmt, ...) {
  char buf[160];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf) - 1, fmt, ap);
  va_end(ap);
  if (n < 0) return;
  if (n > (int)sizeof(buf) - 2) n = sizeof(buf) - 2;
  buf[n++] = '\n';
  tud_cdc_write(buf, (uint32_t)n);
  tud_cdc_write_flush();
}

static void keep_alive(void) {
  recovery_task();
  tud_task();
}

static void handle_local(char *text) {
  char *cmd = strtok(text, " ");
  bool known = true;
  if (!cmd) {
    known = false;
    reply("err empty command");
  } else if (!strcmp(cmd, "!ping")) {
    reply("ok mcp-kvm-bridge %s", FW_VERSION);
  } else if (!strcmp(cmd, "!info")) {
    reply("ok ver=%s wire=%d pin=%d baud=%d up=%lu hang=%d soft=%d failsafe=%d", FW_VERSION,
          wire_level(), KVM_WIRE_PIN, KVM_WIRE_BAUD,
          (unsigned long)to_ms_since_boot(get_absolute_time()), recovery_reset_by_hang(),
          recovery_reset_soft(), recovery_failsafe_armed());
  } else if (!strcmp(cmd, "!selftest")) {
    int good = wire_selftest();
    // The target board saw the test bytes as the start of a line: end that
    // line and discard its complaint, so the next command starts clean.
    wire_write("\n", 1);
    absolute_time_t end = make_timeout_time_ms(30);
    while (!time_reached(end)) {
      keep_alive();
      wire_task();
      (void)wire_read();
    }
    reply("ok %d/32 level=%d", good, wire_level());
  } else if (!strcmp(cmd, "!low")) {
    // Holds the wire low: the reset request understood by other firmwares.
    char *tok = strtok(NULL, " ");
    char *end;
    long ms = tok ? strtol(tok, &end, 10) : 0;
    if (!tok || *end || ms < 1 || ms > 10000) {
      reply("err usage: !low 1..10000");
    } else {
      wire_hold_low((uint32_t)ms, keep_alive);
      reply("ok");
    }
  } else if (!strcmp(cmd, "!bootsel")) {
    reply("ok");
    recovery_bootsel_soon();
  } else if (!strcmp(cmd, "!reboot")) {
    reply("ok");
    recovery_reboot_soon();
  } else {
    known = false;
    reply("err unknown bridge command");
  }
  if (known) recovery_command_seen();
}

static void usb_to_wire(void) {
  while (tud_cdc_available()) {
    int c = tud_cdc_read_char();
    if (c < 0) break;
    if (c == '\n' || c == '\r') {
      if (line_overflow) {
        reply("err line too long");
      } else if (line_len && line[0] == '!') {
        line[line_len] = 0;
        handle_local(line);
      } else if (line_len) {
        line[line_len++] = '\n';
        if (!wire_write(line, line_len)) reply("err bridge busy");
      }
      line_len = 0;
      line_overflow = false;
    } else if (line_len < LINE_MAX - 2) {
      line[line_len++] = (char)c;
    } else {
      line_overflow = true;
    }
  }
}

static void wire_to_usb(void) {
  int c;
  bool any = false;
  while (tud_cdc_write_available() && (c = wire_read()) >= 0) {
    tud_cdc_write_char((char)c);
    any = true;
  }
  if (any) tud_cdc_write_flush();
}

// Opening the serial port at 1200 baud enters BOOTSEL without the parser.
void tud_cdc_line_coding_cb(uint8_t itf, cdc_line_coding_t const *coding) {
  (void)itf;
  if (coding->bit_rate == 1200) recovery_bootsel_soon();
}

int main(void) {
  recovery_init();
  tud_init(0);
  wire_init(KVM_WIRE_PIN, KVM_WIRE_BAUD);
  while (true) {
    recovery_task();
    tud_task();
    wire_task();
    usb_to_wire();
    wire_to_usb();
  }
}
