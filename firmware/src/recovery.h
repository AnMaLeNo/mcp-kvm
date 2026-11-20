#ifndef RECOVERY_H_
#define RECOVERY_H_

#include <stdbool.h>

// Keeps a board reachable for a new firmware without a hand on its BOOT button:
//
// 1. A hang trips the watchdog and reboots. Three hangs in a row drop the
//    board into the BOOTSEL bootloader.
// 2. After a flash or a reboot command, if no valid command arrives within a
//    minute the board returns to BOOTSEL: a firmware that cannot be talked to
//    undoes itself.

void recovery_init(void);          // first thing in main()
void recovery_task(void);          // every turn of the main loop; feeds the watchdog
void recovery_command_seen(void);  // a valid command arrived: the firmware is reachable
void recovery_bootsel_soon(void);  // enter BOOTSEL in 100 ms, leaving time to reply
void recovery_reboot_soon(void);

bool recovery_reset_by_hang(void); // previous run ended in a watchdog timeout
bool recovery_reset_soft(void);    // previous run ended in a flash or a reboot command
bool recovery_failsafe_armed(void);

#endif
