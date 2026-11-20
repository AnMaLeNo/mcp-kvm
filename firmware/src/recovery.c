#include "recovery.h"
#include "hardware/structs/watchdog.h"
#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/time.h"

#define WATCHDOG_MS 3000
#define BOOT_COUNT_MAGIC 0x4b564d00u // "KVM", low byte = consecutive hangs
#define BOOT_COUNT_LIMIT 3
#define HEALTHY_AFTER_MS 10000
#define FAILSAFE_MS 60000

static bool reset_by_hang;
static bool reset_soft;
static bool failsafe_armed;
static bool healthy_marked;
static uint32_t bootsel_at_ms;
static uint32_t reboot_at_ms;

static uint32_t now_ms(void) {
  return to_ms_since_boot(get_absolute_time());
}

void recovery_init(void) {
  reset_by_hang = watchdog_enable_caused_reboot();
  reset_soft = watchdog_caused_reboot() && !reset_by_hang;

  uint32_t hangs = 0;
  uint32_t scratch = watchdog_hw->scratch[0];
  if (reset_by_hang) {
    hangs = ((scratch & 0xffffff00u) == BOOT_COUNT_MAGIC) ? (scratch & 0xff) + 1 : 1;
  }
  if (hangs >= BOOT_COUNT_LIMIT) {
    watchdog_hw->scratch[0] = 0;
    reset_usb_boot(0, 0);
  }
  watchdog_hw->scratch[0] = BOOT_COUNT_MAGIC | hangs;
  failsafe_armed = reset_soft;
  watchdog_enable(WATCHDOG_MS, true);
}

void recovery_task(void) {
  uint32_t t = now_ms();
  watchdog_update();
  if (!healthy_marked && t > HEALTHY_AFTER_MS) {
    watchdog_hw->scratch[0] = BOOT_COUNT_MAGIC;
    healthy_marked = true;
  }
  if (failsafe_armed && t > FAILSAFE_MS) reset_usb_boot(0, 0);
  if (bootsel_at_ms && t >= bootsel_at_ms) reset_usb_boot(0, 0);
  if (reboot_at_ms && t >= reboot_at_ms) watchdog_reboot(0, 0, 0);
}

void recovery_command_seen(void) { failsafe_armed = false; }
void recovery_bootsel_soon(void) { bootsel_at_ms = now_ms() + 100; }
void recovery_reboot_soon(void) { reboot_at_ms = now_ms() + 100; }
bool recovery_reset_by_hang(void) { return reset_by_hang; }
bool recovery_reset_soft(void) { return reset_soft; }
bool recovery_failsafe_armed(void) { return failsafe_armed; }
