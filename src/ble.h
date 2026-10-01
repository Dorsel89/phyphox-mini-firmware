#ifndef _BLE_H   /* Include guard */
#define _BLE_H

#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/device.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/gap.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/services/bas.h>
#include <zephyr/sys/byteorder.h>

#include "sensors.h"

#include "hdc.h"
#include "bmpZephyr.h"
#include "lsm6dsr.h"
#include "stcc4Zephyr.h"
#include "event.h"
#include "datalog.h"

#define DEVICE_NAME "phyfob"

#define PHYFOB_CONN_PARAMTER 0x01
#define PHYFOB_CUSTOM_NAME 0x02

extern struct bt_conn *last_connection;

extern uint8_t config_data[20];

void init_ble();

void send_data(uint8_t ID, float* DATA,uint8_t LEN);

/* Swaps the advertising payload to a BTHome v2 packet for a few seconds so
 * that receivers which never connect (Home Assistant and friends) pick up
 * the values. sensor_mask uses the DATALOG_SENSOR_* bits and decides which
 * of the four arguments actually make it into the packet. Does nothing
 * unless bthome_set_enabled(true) was called. */
void bthome_publish(uint8_t sensor_mask, float co2, float temperature,
		    float humidity, float pressure);
/* Driven by DATALOG_CMD_BTHOME on the datalog config characteristic. */
void bthome_set_enabled(bool enable);

//void en_logging(bool b);

extern void set_coincell_level(uint8_t val);
uint8_t phyfob_config_received(struct bt_conn *conn);

extern bool BLE_PARAMETER_UPDATED;

extern bool notify_enabled;
void ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value);

static struct bt_uuid_128 data_service_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1001, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));
static struct bt_uuid_128 event_service_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf0001, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));
//BMP
static struct bt_uuid_128 bmp_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1007, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));
static struct bt_uuid_128 bmp_cnfg = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1008, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a)); 
//LSM
static struct bt_uuid_128 lsm_gyr_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1000, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));
static struct bt_uuid_128 lsm_acc_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1001, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));
static struct bt_uuid_128 lsm_cnfg = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1002, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a)); 
//HDC
static struct bt_uuid_128 hdc_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1005, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));
static struct bt_uuid_128 hdc_cnfg = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1006, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));
//STCC4
static struct bt_uuid_128 stcc4_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf100b, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));
static struct bt_uuid_128 stcc4_cnfg = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf100c, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));

static struct bt_uuid_128 phyfob_cnfg = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1022, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));

//DATALOG
static struct bt_uuid_128 datalog_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1010, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));
static struct bt_uuid_128 datalog_cnfg = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf1011, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a));

static struct bt_uuid_128 event_uuid = BT_UUID_INIT_128(BT_UUID_128_ENCODE(0xcddf0004, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a)); 


#endif