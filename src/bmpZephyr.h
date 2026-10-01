#ifndef _BMPZEPHYR_H
#define _BMPZEPHYR_H

#include <zephyr/kernel.h>
#include <stdint.h>
#include "bmp5.h"
#include "bmp5_defs.h"
#include <zephyr/drivers/i2c.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/sensor.h>

#include "sensors.h"


const static struct device *bmp_dev = DEVICE_DT_GET(DT_ALIAS(i2c));

static struct sensor_value bmp_press, bmp_temp;

#define BMP_INT DT_NODELABEL(button1)

static struct k_work work_bmp;
static struct k_work config_work_bmp;

extern int8_t init_bmp();
extern void submit_config_bmp();
extern uint8_t sleep_bmp(bool SLEEP);
extern uint8_t bmp_loggingmode();
extern void bmp_read_once(float *pressure, float *temperature);

static void start_logging();

// ----------------
// Driver variables
// ----------------
#define BMP5_SENSOR_OK 0x00
#define BMP581_I2C_ADDR	BMP5_I2C_ADDR_PRIM
static int8_t bmpResult;
static uint8_t dByte;


static struct bmp5_dev bmp581_dev;
//static struct bmp5_data myData;

static int16_t counter=0;

#endif