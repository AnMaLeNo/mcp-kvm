#include "wire.h"
#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/pio.h"
#include "hardware/sync.h"
#include "pico/time.h"
#include "wire.pio.h"

#define RING 1024 // power of two

static PIO pio = pio0;
static uint sm_tx, sm_rx;
static uint wire_pin;

static uint8_t rx_ring[RING];
static volatile uint16_t rx_head, rx_tail;
static uint8_t tx_ring[RING];
static uint16_t tx_head, tx_tail;

// The line is shared, so every byte sent comes back through the receiver.
// That many received bytes are dropped instead of being handed to the caller.
static volatile uint32_t echo_expected;
static volatile bool selftest_running;
static volatile uint32_t last_rx_us;
static uint32_t last_tx_us;

static void rx_irq(void) {
  while (!pio_sm_is_rx_fifo_empty(pio, sm_rx)) {
    uint8_t byte = (uint8_t)(pio_sm_get(pio, sm_rx) >> 24);
    last_rx_us = time_us_32();
    if (echo_expected && !selftest_running) {
      echo_expected--;
      continue;
    }
    uint16_t next = (rx_head + 1) & (RING - 1);
    if (next != rx_tail) {
      rx_ring[rx_head] = byte;
      rx_head = next;
    }
  }
}

void wire_init(unsigned pin, unsigned baud) {
  wire_pin = pin;
  float div = (float)clock_get_hz(clk_sys) / (8.0f * (float)baud);

  pio_gpio_init(pio, pin);
  gpio_pull_up(pin);

  uint offset = pio_add_program(pio, &wire_tx_program);
  sm_tx = pio_claim_unused_sm(pio, true);
  pio_sm_set_pins_with_mask(pio, sm_tx, 0, 1u << pin);
  pio_sm_set_consecutive_pindirs(pio, sm_tx, pin, 1, false);
  pio_sm_config c = wire_tx_program_get_default_config(offset);
  sm_config_set_out_shift(&c, true, false, 32);
  sm_config_set_out_pins(&c, pin, 1);
  sm_config_set_sideset_pins(&c, pin);
  sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_TX);
  sm_config_set_clkdiv(&c, div);
  pio_sm_init(pio, sm_tx, offset, &c);
  pio_sm_set_enabled(pio, sm_tx, true);

  offset = pio_add_program(pio, &wire_rx_program);
  sm_rx = pio_claim_unused_sm(pio, true);
  c = wire_rx_program_get_default_config(offset);
  sm_config_set_in_pins(&c, pin);
  sm_config_set_jmp_pin(&c, pin);
  sm_config_set_in_shift(&c, true, false, 32);
  sm_config_set_fifo_join(&c, PIO_FIFO_JOIN_RX);
  sm_config_set_clkdiv(&c, div);
  pio_sm_init(pio, sm_rx, offset, &c);

  // Received bytes are moved out of the 8-byte hardware queue by interrupt,
  // so a slow turn of the main loop cannot lose any.
  pio_set_irq0_source_enabled(pio, (enum pio_interrupt_source)(pis_sm0_rx_fifo_not_empty + sm_rx), true);
  irq_set_exclusive_handler(PIO0_IRQ_0, rx_irq);
  irq_set_enabled(PIO0_IRQ_0, true);
  pio_sm_set_enabled(pio, sm_rx, true);
}

static void send_byte(uint8_t byte) {
  uint32_t state = save_and_disable_interrupts();
  echo_expected++;
  restore_interrupts(state);
  pio_sm_put(pio, sm_tx, (uint8_t)~byte);
  last_tx_us = time_us_32();
}

void wire_task(void) {
  while (tx_tail != tx_head && !pio_sm_is_tx_fifo_full(pio, sm_tx)) {
    send_byte(tx_ring[tx_tail]);
    tx_tail = (tx_tail + 1) & (RING - 1);
  }
  // An echo lost to a collision would make the count eat a real byte later:
  // once the line has been quiet for a while, nothing is owed any more.
  uint32_t now = time_us_32();
  if (echo_expected && tx_tail == tx_head && pio_sm_is_tx_fifo_empty(pio, sm_tx) &&
      now - last_tx_us > 5000 && now - last_rx_us > 5000) {
    uint32_t state = save_and_disable_interrupts();
    echo_expected = 0;
    restore_interrupts(state);
  }
}

bool wire_write(const void *data, uint32_t len) {
  uint32_t used = (uint16_t)(tx_head - tx_tail) & (RING - 1);
  if (len > RING - 1 - used) return false;
  const uint8_t *bytes = data;
  for (uint32_t i = 0; i < len; i++) {
    tx_ring[tx_head] = bytes[i];
    tx_head = (tx_head + 1) & (RING - 1);
  }
  wire_task();
  return true;
}

int wire_read(void) {
  if (rx_tail == rx_head) return -1;
  uint8_t byte = rx_ring[rx_tail];
  rx_tail = (rx_tail + 1) & (RING - 1);
  return byte;
}

bool wire_level(void) {
  return gpio_get(wire_pin);
}

void wire_hold_low(uint32_t ms, void (*while_waiting)(void)) {
  // Take the pin away from the transmitter for the duration.
  gpio_init(wire_pin);
  gpio_put(wire_pin, 0);
  gpio_set_dir(wire_pin, GPIO_OUT);
  absolute_time_t end = make_timeout_time_ms(ms);
  while (!time_reached(end)) {
    if (while_waiting) while_waiting();
  }
  gpio_set_dir(wire_pin, GPIO_IN);
  pio_gpio_init(pio, wire_pin);
  gpio_pull_up(wire_pin);
}

int wire_selftest(void) {
  int good = 0;
  selftest_running = true;
  rx_tail = rx_head;
  for (int i = 0; i < 32; i++) {
    uint8_t byte = (uint8_t)(0x55 ^ (i * 7));
    pio_sm_put_blocking(pio, sm_tx, (uint8_t)~byte);
    absolute_time_t end = make_timeout_time_ms(3);
    int got = -1;
    while (got < 0 && !time_reached(end)) got = wire_read();
    if (got == byte) good++;
  }
  selftest_running = false;
  return good;
}
