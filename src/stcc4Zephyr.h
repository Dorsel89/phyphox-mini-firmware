#ifndef _STCC4ZEPHYR_H
#define _STCC4ZEPHYR_H


#include <zephyr/kernel.h>
#include <stdint.h>
#include "sensirion_common.h"
#include "sensirion_i2c_hal.h"
#include "stcc4_i2c.h"

#include "sensors.h"
#include <math.h>

#define STCC4_CALIBRATION 0x02
const static struct device *stcc4_dev = DEVICE_DT_GET(DT_ALIAS(i2c));

static struct k_timer timer_stcc4;
static struct k_work work_stcc4;
static struct k_work config_work_stcc4;

extern int8_t init_stcc4();
extern void submit_config_stcc4();
extern uint8_t sleep_stcc4(bool SLEEP);

void send_data_stcc4(struct k_work *work);
void stcc4_data_ready();
void set_config_stcc4(struct k_work *work);

extern uint8_t stcc4_compensate(float t, float rh);
extern void stcc4_read_once(float *co2);
#endif