#ifndef _BAS_H /* Include guard */
#define _BAS_H

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/adc.h>
#include <hal/nrf_saadc.h>
#include <zephyr/devicetree/io-channels.h>
#include <stdio.h>
#include <math.h>

#define ADC_RESOLUTION     10
#define ADC_GAIN           ADC_GAIN_1_6
#define ADC_REFERENCE      ADC_REF_INTERNAL
#define ADC_ACQUISITION_TIME ADC_ACQ_TIME_DEFAULT

#define ADC_NODE DT_NODELABEL(adc)
#define ADC_CHANNEL 4

#define ADC_1ST_CHANNEL_ID 7
#define ADC_1ST_CHANNEL_INPUT NRF_SAADC_INPUT_AIN7

#define BUFFER_SIZE 1

const static struct device *adc_dev;

extern void init_BAS();
static struct k_timer timer_bas;
static struct k_work work_bas;

void update_coincell_level(struct k_work *work);
float getVoltage();

uint8_t battery_level(float);
void time_to_update_battery_service();

#endif