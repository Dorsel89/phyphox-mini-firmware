#ifndef _DATALOG_H
#define _DATALOG_H

#include <zephyr/kernel.h>
#include <stdbool.h>
#include <stdint.h>

#define DATALOG_CMD_STOP   0x00
#define DATALOG_CMD_START  0x01
#define DATALOG_CMD_DUMP   0x02
#define DATALOG_CMD_ERASE  0x03
#define DATALOG_CMD_BTHOME 0x04

#define DATALOG_SENSOR_CO2      0x01
#define DATALOG_SENSOR_TEMP     0x02
#define DATALOG_SENSOR_HUMIDITY 0x04
#define DATALOG_SENSOR_PRESSURE 0x08

/* Restores the settings persisted in flash (sensor mask, interval, whether
 * logging was running, BTHome advertising) and resumes logging if it was
 * active, so a battery change does not lose the user's configuration.
 * Returns false if nothing was stored yet (or the flash area is
 * unusable), in which case the caller decides on a default. */
bool init_datalog(void);
/* sensors_mask is only used by DATALOG_CMD_START. The third parameter is
 * reused for three different purposes depending on cmd:
 * - DATALOG_CMD_START: logging interval in seconds.
 * - DATALOG_CMD_DUMP: maximum age of records to send, in minutes
 *   (0 = no limit, send the whole log).
 * - DATALOG_CMD_BTHOME: 0 disables the BTHome advertising bursts, anything
 *   else enables them. Like the sensor mask and the interval, the setting
 *   is persisted and restored on the next boot.
 * Ignored for DATALOG_CMD_STOP/DATALOG_CMD_ERASE. */
void datalog_configure(uint8_t cmd, uint8_t sensors_mask, uint16_t interval_s);

/* The settings currently in effect - which, after a reboot, are the ones
 * restored from flash rather than any the app has sent this session. */
struct datalog_state {
    bool running;
    uint8_t mask;
    uint16_t interval_s;
    bool bthome;
};

/* Lets the app find out what the fob is actually logging, e.g. to show the
 * configuration that survived a battery change instead of a blank form. */
void datalog_get_state(struct datalog_state *state);

#endif
