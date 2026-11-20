#ifndef WIRE_H_
#define WIRE_H_

#include <stdbool.h>
#include <stdint.h>

// Serial link between the two boards over one signal wire plus ground.
// See wire.pio. Bytes this board sends are not handed back to it.

void wire_init(unsigned pin, unsigned baud);

// Call often from the main loop: moves queued bytes to the transmitter.
void wire_task(void);

// Queues bytes for sending. Returns false, sending nothing, if they do not fit.
bool wire_write(const void *data, uint32_t len);

// Next received byte, or -1 if there is none.
int wire_read(void);

// Current level of the line (1 when idle).
bool wire_level(void);

// Holds the line low for the given time, then releases it. Blocks; the caller
// keeps the watchdog fed through the callback.
void wire_hold_low(uint32_t ms, void (*while_waiting)(void));

// Sends 32 bytes and reads them back from the line. Returns how many came
// back intact: 32 when the pin and its pull-up work and nothing holds the line.
int wire_selftest(void);

#endif
