#include "ble.h"

#include <math.h>
#include <stdio.h>
#include <hal/nrf_nvmc.h>
#include <nrfx.h>
#if defined(CONFIG_MCUBOOT_IMG_MANAGER)
#include <zephyr/dfu/mcuboot.h>
#endif

struct bt_conn *last_connection;
uint8_t config_data[20] = {0};
bool BLE_PARAMETER_UPDATED;
bool notify_enabled;

void ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
	notify_enabled = (value == BT_GATT_CCC_NOTIFY) ? 1 : 0;
}

BMP bmp_data;
LSM lsm_data;
STCC4 stcc4_data;
LOGGING logging;
PHYPHOX_EVENT event_data;
HDC hdc_data;
PHYFOB phyfob_config;
DATALOG_CONFIG datalog_config;

static struct bt_gatt_attr *attr_lsm_acc;
static struct bt_gatt_attr *attr_lsm_gyr;

static struct bt_le_conn_param custom_param;

DATALOGGING LOG;

volatile float global_timestamp = NAN;

static const struct bt_le_adv_param adv_param_normal = {
	.options = BT_LE_ADV_OPT_CONNECTABLE | BT_LE_ADV_OPT_USE_NAME | BT_LE_ADV_OPT_FORCE_NAME_IN_AD,
	.interval_min = 3200,
	.interval_max = 3360,
};

static const struct bt_le_adv_param adv_param_fast = {
	.options = BT_LE_ADV_OPT_CONNECTABLE | BT_LE_ADV_OPT_USE_NAME | BT_LE_ADV_OPT_FORCE_NAME_IN_AD,
	.interval_min = 80,
	.interval_max = 100,
};

/* No name options here: zephyr appends the device name to the advertising
 * data on every start, which would push the BTHome service data past the
 * 31-byte limit. The name is dropped for the duration of a burst only - the
 * 128-bit phyphox UUID lives in the scan response, a separate 31-byte
 * packet, and stays visible throughout. 500 ms rather than the usual 2 s so
 * that a duty-cycled scanner (e.g. an ESPHome bluetooth proxy, ~9% of the
 * time listening) still catches several packets inside the burst window. */
static const struct bt_le_adv_param adv_param_bthome = {
	.options = BT_LE_ADV_OPT_CONNECTABLE,
	.interval_min = 800,
	.interval_max = 880,
};

static const struct bt_data ad[] = {
	/*
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
	BT_DATA_BYTES(BT_DATA_UUID16_ALL,
		BT_UUID_16_ENCODE(BT_UUID_BAS_VAL)), //battery service
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, 
		BT_UUID_128_ENCODE(0xcddf1001, 0x30f7, 0x4671, 0x8b43, 0x5e40ba53514a))
	*/
	BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
	BT_DATA_BYTES(BT_DATA_UUID16_ALL,BT_UUID_16_ENCODE(BT_UUID_BAS_VAL)),
};

// SCAN RESPONSE DATA
static const struct bt_data sd[] = {
BT_DATA_BYTES(BT_DATA_UUID128_ALL,
		      0x84, 0xaa, 0x60, 0x74, 0x52, 0x8a, 0x8b, 0x86,
		      0xd3, 0x4c, 0xb7, 0x1d, 0x1d, 0xdc, 0x53, 0x8d),
//BT_DATA(BT_DATA_NAME_COMPLETE, DEVICE_NAME, sizeof(DEVICE_NAME) - 1),
};

/* BTHome v2 (https://bthome.io/format/), unencrypted, regular intervals.
 * The device name does not fit alongside the service data in the 31-byte
 * advertising payload, so instead of permanently sacrificing one of the two,
 * the payload is swapped to BTHome only for a short burst whenever the
 * datalog module has produced a fresh record - see bthome_publish(). */
#define BTHOME_SERVICE_UUID   0xfcd2
/* bit 0 = not encrypted, bit 2 = regular intervals, bits 5-7 = version 2 */
#define BTHOME_DEVICE_INFO    0x40

#define BTHOME_ID_PACKET      0x00
#define BTHOME_ID_TEMPERATURE 0x02
#define BTHOME_ID_HUMIDITY    0x03
#define BTHOME_ID_PRESSURE    0x04
#define BTHOME_ID_CO2         0x12

/* UUID (2) + device info (1) + packet id (2) + temperature (3) +
 * humidity (3) + pressure (4) + co2 (3). With the 3-byte flags element that
 * is 23 of the 31 available bytes. */
#define BTHOME_MAX_SVC_DATA   18
/* Only the UUID and the device info byte are always present. */
#define BTHOME_HEADER_LEN     3

#define BTHOME_BURST_DURATION K_SECONDS(10)

static uint8_t bthome_svc_data[BTHOME_MAX_SVC_DATA] = {
	BT_UUID_16_ENCODE(BTHOME_SERVICE_UUID),
	BTHOME_DEVICE_INFO,
};
static uint8_t bthome_packet_id;
/* Off until a phone switches it on via DATALOG_CMD_BTHOME - a device that
 * is only ever used with the phyphox app should not pay for advertising
 * nobody listens to. Not persisted, so every reboot starts out disabled. */
static bool bthome_enabled;

/* Not const: data_len is fixed up per burst so that only the sensors that
 * were actually measured end up in the packet. */
static struct bt_data ad_bthome[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
	BT_DATA(BT_DATA_SVC_DATA16, bthome_svc_data, BTHOME_HEADER_LEN),
};

/* Advertising has three payload/timing variants that all reuse the same
 * scan response, plus "off" while connected. Every switch goes through
 * apply_advertising() so the burst and the post-disconnect fast window
 * cannot overwrite each other's state. */
enum adv_state {
	ADV_OFF,
	ADV_NORMAL,
	ADV_FAST,
	ADV_BTHOME,
};

static enum adv_state adv_current = ADV_OFF;
static enum adv_state adv_requested = ADV_NORMAL;
static bool adv_connected;
static struct k_work adv_work;

static void apply_advertising(enum adv_state state)
{
	int err;

	bt_le_adv_stop();
	adv_current = ADV_OFF;

	/* A burst or fast-window revert may still be queued when a phone
	 * connects - it must not bring advertising back up behind the
	 * connection's back. */
	if (state == ADV_OFF || adv_connected) {
		return;
	}

	if (state == ADV_BTHOME) {
		err = bt_le_adv_start(&adv_param_bthome, ad_bthome, ARRAY_SIZE(ad_bthome),
				      sd, ARRAY_SIZE(sd));
	} else {
		const struct bt_le_adv_param *param =
			(state == ADV_FAST) ? &adv_param_fast : &adv_param_normal;

		err = bt_le_adv_start(param, ad, ARRAY_SIZE(ad), sd, ARRAY_SIZE(sd));
	}

	if (err) {
		printk("Advertising failed to start in state %d (err %d)\n\r", state, err);
		return;
	}
	adv_current = state;
}

static void adv_work_handler(struct k_work *work)
{
	apply_advertising(adv_requested);
}

/* bt_le_adv_start() may block, so state changes coming from a timer handler
 * (ISR context) have to be deferred to the system workqueue. */
static void request_advertising(enum adv_state state)
{
	adv_requested = state;
	k_work_submit(&adv_work);
}

/* Ends both the post-disconnect fast window and a BTHome burst - the two
 * can never be active at the same time (see bthome_publish()), so a single
 * timer meaning "go back to normal advertising" is enough. */
static void adv_timer_handler(struct k_timer *timer)
{
	request_advertising(ADV_NORMAL);
}

K_TIMER_DEFINE(adv_timer, adv_timer_handler, NULL);

void update_phy(struct bt_conn *conn) {

	int err;
	struct bt_conn_le_phy_param phy_param = {
		.options = BT_CONN_LE_PHY_OPT_NONE,
		.pref_tx_phy = BT_GAP_LE_PHY_2M,
		.pref_rx_phy = BT_GAP_LE_PHY_2M
	};
	err = bt_conn_le_phy_update(conn, &phy_param);
}

static ssize_t read_u16(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf, uint16_t len, uint16_t offset)
{
	uint8_t *value = attr->user_data;

	return bt_gatt_attr_read(conn, attr, buf, len, offset, value, sizeof(value));
}

static ssize_t config_submits(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf, uint16_t len, uint16_t offset,uint8_t flags)
{
	uint8_t *value = attr->user_data;
	if (offset + len > sizeof(config_data)) {
		return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
	}
	memcpy(value + offset, buf, len);
	
	if(attr->uuid == &bmp_cnfg.uuid){
		submit_config_bmp();
	}
	if(attr->uuid == &hdc_cnfg.uuid){
		submit_config_hdc();
	}
	if(attr->uuid == &lsm_cnfg.uuid){
		adjust_lsm_configuration(NULL);
	}
	if(attr->uuid == &stcc4_cnfg.uuid){
		submit_config_stcc4();
	}
	if(attr->uuid == &event_uuid.uuid){
		phyphox_event_received();
	}
	if(attr->uuid == &phyfob_cnfg.uuid){
		phyfob_config_received(conn);
	}
	if(attr->uuid == &datalog_cnfg.uuid){
		printk("ble: datalog_cnfg write, len=%u offset=%u raw=%02x %02x %02x %02x\r\n",
		       len, offset, datalog_config.config[0], datalog_config.config[1],
		       datalog_config.config[2], datalog_config.config[3]);
		/* phyphox writes byte 0 (cmd), the mask byte and the two parameter
		 * bytes as separate BLE writes, each independently reaching this
		 * callback. Only act once byte 0 itself arrives - the experiments
		 * are built to always write it last, so mask/param are already up
		 * to date by then. Reacting to every partial write would fire
		 * datalog_configure() multiple times with incomplete/stale data in
		 * between (e.g. an unintended full dump before the real request
		 * with the correct parameters lands). */
		if (offset == 0) {
			uint16_t param = datalog_config.config[2] | (datalog_config.config[3] << 8);
			datalog_configure(datalog_config.config[0], datalog_config.config[1], param);
		}
	}
	return len;
};

/* The datalog config characteristic is written as a command (cmd, mask,
 * param_lo, param_hi - see datalog_configure()), but reads back the
 * configuration actually in effect, so the app can show what a fob is
 * logging after it restored its settings from flash on a battery change:
 *   byte 0    : 1 while logging runs, 0 while stopped - that is exactly the
 *               DATALOG_CMD_START/DATALOG_CMD_STOP value which would
 *               recreate the current state
 *   byte 1    : sensor mask (DATALOG_SENSOR_*)
 *   bytes 2-3 : logging interval in seconds, little endian - the same
 *               offsets the interval is written at
 *   byte 4    : 1 if BTHome advertising is enabled
 */
static ssize_t read_datalog_config(struct bt_conn *conn, const struct bt_gatt_attr *attr,
				   void *buf, uint16_t len, uint16_t offset)
{
	struct datalog_state state;
	uint8_t value[5];

	datalog_get_state(&state);

	value[0] = state.running ? DATALOG_CMD_START : DATALOG_CMD_STOP;
	value[1] = state.mask;
	sys_put_le16(state.interval_s, &value[2]);
	value[4] = state.bthome ? 1 : 0;

	printk("ble: datalog_cnfg read, running=%u mask=0x%02x interval=%us bthome=%u\r\n",
	       value[0], value[1], state.interval_s, value[4]);

	return bt_gatt_attr_read(conn, attr, buf, len, offset, value, sizeof(value));
}

BT_GATT_SERVICE_DEFINE(phyphox_gatt, 
	BT_GATT_PRIMARY_SERVICE(&data_service_uuid),
	//BMP384 
	BT_GATT_CHARACTERISTIC(&bmp_uuid,					
			       BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_READ,
			       read_u16, NULL, &bmp_data.array[0]),
	BT_GATT_CCC(ccc_cfg_changed,
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
 	BT_GATT_CHARACTERISTIC(&bmp_cnfg,					
			       BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_WRITE,
			       NULL, config_submits, &bmp_data.config[0]),
	BT_GATT_CCC(ccc_cfg_changed,	//notification handler
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
//lsm323 - GYROSCOPE
	BT_GATT_CHARACTERISTIC(&lsm_gyr_uuid,					
			       BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_READ,
			       read_u16, NULL, &lsm_data.gyr_array[0]),
	BT_GATT_CCC(ccc_cfg_changed,
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
BT_GATT_CHARACTERISTIC(&lsm_acc_uuid,					
			       BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_READ,
			       read_u16, NULL, &lsm_data.acc_array[0]),
	BT_GATT_CCC(ccc_cfg_changed,
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),		
    BT_GATT_CHARACTERISTIC(&lsm_cnfg,					
			       BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_WRITE,
			       NULL, config_submits, &lsm_data.config[0]),
				   
	BT_GATT_CCC(ccc_cfg_changed,	//notification handler
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),	
	//HDC
	BT_GATT_CHARACTERISTIC(&hdc_uuid,					
			       BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_READ,
			       read_u16, NULL, &hdc_data.array[0]),
	BT_GATT_CCC(ccc_cfg_changed,
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
    BT_GATT_CHARACTERISTIC(&hdc_cnfg,					
			       BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_WRITE,
			       NULL, config_submits, &hdc_data.config[0]),
	BT_GATT_CCC(ccc_cfg_changed,	//notification handler
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	//STCC4
	BT_GATT_CHARACTERISTIC(&stcc4_uuid,					
			       BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_READ,
			       read_u16, NULL, &stcc4_data.array[0]),
	BT_GATT_CCC(ccc_cfg_changed,
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
    BT_GATT_CHARACTERISTIC(&stcc4_cnfg,					
			       BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_WRITE,
			       NULL, config_submits, &stcc4_data.config[0]),
	BT_GATT_CCC(ccc_cfg_changed,	//notification handler
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	//phyfob config
	BT_GATT_CHARACTERISTIC(&phyfob_cnfg,
			       BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_WRITE,
			       NULL, config_submits, &phyfob_config.config[0]),
	BT_GATT_CCC(ccc_cfg_changed,	//notification handler
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	//DATALOG
	BT_GATT_CHARACTERISTIC(&datalog_uuid,
			       BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_READ,
			       read_u16, NULL, &datalog_config.array[0]),
	BT_GATT_CCC(ccc_cfg_changed,
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	BT_GATT_CHARACTERISTIC(&datalog_cnfg,
			       BT_GATT_CHRC_READ | BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_READ | BT_GATT_PERM_WRITE,
			       read_datalog_config, config_submits, &datalog_config.config[0]),
	BT_GATT_CCC(ccc_cfg_changed,	//notification handler
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
	//EVENT SERVICE
	BT_GATT_PRIMARY_SERVICE(&event_service_uuid),
	BT_GATT_CHARACTERISTIC(&event_uuid,					
			       BT_GATT_CHRC_WRITE | BT_GATT_CHRC_NOTIFY,
			       BT_GATT_PERM_WRITE,
			       NULL, config_submits, &event_data.config[0]),
	BT_GATT_CCC(ccc_cfg_changed,	//notification handler
		    BT_GATT_PERM_READ | BT_GATT_PERM_WRITE)				
);

extern void bthome_set_enabled(bool enable)
{
	printk("bthome: advertising %s\r\n", enable ? "enabled" : "disabled");
	bthome_enabled = enable;

	/* Without this, a burst that happens to be running would keep pushing
	 * values out for up to another burst duration after being switched
	 * off. */
	if (!enable && adv_current == ADV_BTHOME) {
		k_timer_stop(&adv_timer);
		request_advertising(ADV_NORMAL);
	}
}

extern void bthome_publish(uint8_t sensor_mask, float co2, float temperature,
			   float humidity, float pressure)
{
	if (!bthome_enabled) {
		return;
	}

	/* The 10 s fast-advertising window that disconnected() opens for quick
	 * reconnects owns adv_timer as well, so a burst landing inside it would
	 * cut the reconnect window short. Skipping is harmless: the record is
	 * written to flash either way and the next datalog tick publishes
	 * again. */
	if (adv_current == ADV_FAST) {
		printk("bthome: burst skipped, fast advertising active\r\n");
		return;
	}

	uint8_t pos = BTHOME_HEADER_LEN;

	/* Every packet of a burst repeats the same id, so a receiver can tell
	 * the retransmissions apart from the next actual measurement. */
	bthome_svc_data[pos++] = BTHOME_ID_PACKET;
	bthome_svc_data[pos++] = bthome_packet_id++;

	/* Object ids have to be written in ascending order, which is not the
	 * order the datalog module reads the sensors in. */
	if (sensor_mask & DATALOG_SENSOR_TEMP) {
		int16_t val = CLAMP((int32_t)roundf(temperature * 100.0f),
				    INT16_MIN, INT16_MAX);

		bthome_svc_data[pos++] = BTHOME_ID_TEMPERATURE;
		sys_put_le16((uint16_t)val, &bthome_svc_data[pos]);
		pos += 2;
	}
	if (sensor_mask & DATALOG_SENSOR_HUMIDITY) {
		uint16_t val = CLAMP((int32_t)roundf(humidity * 100.0f), 0, UINT16_MAX);

		bthome_svc_data[pos++] = BTHOME_ID_HUMIDITY;
		sys_put_le16(val, &bthome_svc_data[pos]);
		pos += 2;
	}
	if (sensor_mask & DATALOG_SENSOR_PRESSURE) {
		/* bmp_data.pressure is already in hPa, BTHome wants 0.01 hPa
		 * in an unsigned 24-bit field. */
		uint32_t val = CLAMP((int32_t)roundf(pressure * 100.0f), 0, 0xffffff);

		bthome_svc_data[pos++] = BTHOME_ID_PRESSURE;
		sys_put_le24(val, &bthome_svc_data[pos]);
		pos += 3;
	}
	if (sensor_mask & DATALOG_SENSOR_CO2) {
		uint16_t val = CLAMP((int32_t)roundf(co2), 0, UINT16_MAX);

		bthome_svc_data[pos++] = BTHOME_ID_CO2;
		sys_put_le16(val, &bthome_svc_data[pos]);
		pos += 2;
	}

	/* Nothing but the packet id - advertising that carries no information
	 * and would only cost the receiver a wakeup. */
	if (pos == BTHOME_HEADER_LEN + 2) {
		return;
	}

	/* Sending a fixed-size packet instead would publish fabricated zeros
	 * for every sensor that was not part of this record. */
	ad_bthome[1].data_len = pos;

	printk("bthome: burst with mask 0x%02x, %u bytes of service data\r\n",
	       sensor_mask, pos);

	request_advertising(ADV_BTHOME);
	k_timer_start(&adv_timer, BTHOME_BURST_DURATION, K_NO_WAIT);
}
static void bt_ready(void)
{
	BLE_PARAMETER_UPDATED = false;
	printk("Bluetooth initialized\n\r");

	uint16_t serialNumber[1];
	char ascii[1];
	char ascii_custom[4];
	uint8_t* id_address = (uint8_t *)0x10001080;
	memcpy(&serialNumber[0], id_address, 2);
	memcpy(&ascii[0], id_address+2, 1);
	memcpy(&ascii_custom[0], id_address+4, 4);

	if(serialNumber[0]==0x00 || serialNumber[0]==0xffff){
		serialNumber[0]=0;
		ascii[0]='#';
	}
	printk("number: %i \r\n",serialNumber[0]);

	char name[20];
	printk("1: %c 2: %c 3: %c 4: %c \r\n", ascii_custom[0],ascii_custom[1],ascii_custom[2],ascii_custom[3]);
	printk("1: %i 2: %i 3: %i 4: %i \r\n", ascii_custom[0],ascii_custom[1],ascii_custom[2],ascii_custom[3]);
	if(ascii_custom[0] == 0xff && ascii_custom[1] == 0xff && ascii_custom[2] == 0xff && ascii_custom[3] == 0xff){
		sprintf(name, "phyphox:mini %c%02d\n", ascii[0], serialNumber[0]);
	}else{
		sprintf(name, "phyphox:mini %c%c%c%c\n", ascii_custom[3], ascii_custom[2],ascii_custom[1],ascii_custom[0]);	
		printk("neuer custom name: %s \r\n",name);
	}
	
	bt_set_name(name);


	attr_lsm_acc = bt_gatt_find_by_uuid(NULL,0,&lsm_acc_uuid);
	attr_lsm_gyr = bt_gatt_find_by_uuid(NULL,0,&lsm_gyr_uuid);
	apply_advertising(ADV_NORMAL);

	if (adv_current == ADV_NORMAL) {
		printk("Advertising successfully started\n\r");
#if defined(CONFIG_MCUBOOT_IMG_MANAGER)
		/* After an OTA update MCUboot boots the new image only on trial and
		 * swaps back on the next reset unless it gets confirmed. Confirming
		 * used to be left to the phone or the flasher over BLE, so a lost
		 * connection at the wrong moment silently undid the update at the
		 * next battery change. Confirm here: advertising works, so the fob
		 * can take another update if this image turns out to be bad. An
		 * image that crashes before this point still reverts. */
		if (!boot_is_img_confirmed()) {
			int rc = boot_write_img_confirmed();
			printk("mcuboot: image confirmed (%d)\n\r", rc);
		}
#endif
	}
}

static void connected(struct bt_conn *conn, uint8_t err)
{

	RESETTED = true;
	logging.enable = false;
	/* Cut a running BTHome burst short and make sure neither it nor the
	 * fast-advertising window brings advertising back up while connected. */
	k_timer_stop(&adv_timer);
	printk("Device with index %i trying to connect...\n\r",bt_conn_index(conn));

	if (err) {
		/* No connection was established, so disconnected() will never
		 * run to restart advertising - do it here. */
		printk("Connection failed (err 0x%02x)\n\r", err);
		request_advertising(ADV_NORMAL);
		return;
	}

	adv_connected = true;
	apply_advertising(ADV_OFF);

	update_phy(conn);
	printk("Connected\n\r");
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
	adv_connected = false;
	apply_advertising(ADV_FAST);
	k_timer_start(&adv_timer, K_SECONDS(10), K_NO_WAIT); //change back to energy efficient advertising after 10s
	printk("Disconnected (reason 0x%02x)\n\r", reason);

	logging.enable = true;
	enable_lsm(false);
	sleep_bmp(true);
	sleep_stcc4(true);
	sleep_hdc(true);

	BLE_PARAMETER_UPDATED = false;
	RESETTED = true;
}
static void le_param_updated(struct bt_conn *conn, uint16_t interval,
			     uint16_t latency, uint16_t timeout){
	printk("Connection parameters updated.\n\r"
	       " interval: %d, latency: %d, timeout: %d\n\r",
	       interval, latency, timeout);
		   BLE_PARAMETER_UPDATED = true;
}
static void le_phy_updated(struct bt_conn *conn,struct bt_conn_le_phy_info *param){
	if(DEBUG){
		printk("LE PHY updated\n\r");
	}
}
static bool le_param_req(struct bt_conn *conn, struct bt_le_conn_param *param){
	if(DEBUG){
		printk("Connection parameters update request received.\n\r");
		printk("Minimum interval: %d, Maximum interval: %d\n\r",
	       param->interval_min, param->interval_max);
		printk("Latency: %d, Timeout: %d\n\r", param->latency, param->timeout);
	}	
	return true;
}
static void le_data_len_updated(struct bt_conn *conn, struct bt_conn_le_data_len_info *info)
{
    char addr[BT_ADDR_LE_STR_LEN];

    bt_addr_le_to_str(bt_conn_get_dst(conn), addr, sizeof(addr));
	if(DEBUG){
		printk("Data length updated: %s max tx %u (%u us) max rx %u (%u us)\n\r",
           addr, info->tx_max_len, info->tx_max_time, info->rx_max_len,
           info->rx_max_time);
		   }
}
static struct bt_conn_cb conn_callbacks = {
	.connected = connected,
	.disconnected = disconnected,
	.le_param_req = le_param_req,
	.le_param_updated = le_param_updated,
	.le_phy_updated = le_phy_updated,
	.le_data_len_updated = le_data_len_updated,
};

void init_ble(){
	k_work_init(&adv_work, adv_work_handler);
	event_data.RUNNING = false;
	bt_enable(NULL);
	bt_ready();
	bt_conn_cb_register(&conn_callbacks);
	memset(LOG.data,NULL,176*LOG_MULTIPLIER);
	LOG.write_to_position = 0;
	LOG.average_sum = 0;
	LOG.average_n= 0;
	LOG.last_save =0;
};

extern void set_coincell_level(uint8_t val){
	bt_bas_set_battery_level(val);
}

static void parameter_work(struct k_work *work)
{
    bt_conn_le_param_update(last_connection,&custom_param);
}
K_WORK_DELAYABLE_DEFINE(my_delayed_work, parameter_work);


static void nvmc_wait_ready(void)
{
	while (NRF_NVMC->READY == NVMC_READY_READY_Busy) {
	}
}

static int uicr_update_customer(uint32_t customer1)
{
	NRF_UICR_Type backup;

	/* Gesamte UICR sichern, weil ERASEUICR alles löscht */
	memcpy(&backup, NRF_UICR, sizeof(backup));

	/* Gewünschte neue Werte setzen */
	uint32_t* uicr = (uint32_t*)0x10001080;
	backup.CUSTOMER[0] = *uicr;
	backup.CUSTOMER[1] = customer1;

	/* UICR-Erase aktivieren */
	NRF_NVMC->CONFIG = NVMC_CONFIG_WEN_Een;
	nvmc_wait_ready();

	/* Ganze UICR löschen */
	NRF_NVMC->ERASEUICR = NVMC_ERASEUICR_ERASEUICR_Erase;
	nvmc_wait_ready();

	/* Schreibmodus aktivieren */
	NRF_NVMC->CONFIG = NVMC_CONFIG_WEN_Wen;
	nvmc_wait_ready();

	/*
	 * Ganze UICR zurückschreiben.
	 * Nur Wörter != 0xFFFFFFFF müssen programmiert werden.
	 */
	volatile uint32_t *dst = (volatile uint32_t *)NRF_UICR;
	const uint32_t *src = (const uint32_t *)&backup;

	for (size_t i = 0; i < sizeof(NRF_UICR_Type) / sizeof(uint32_t); i++) {
		if (src[i] != 0xFFFFFFFFu) {
			dst[i] = src[i];
			nvmc_wait_ready();
		}
	}

	/* Wieder Read-only */
	NRF_NVMC->CONFIG = NVMC_CONFIG_WEN_Ren;
	nvmc_wait_ready();

	return 0;
}

uint8_t phyfob_config_received(struct bt_conn *conn){
	if(phyfob_config.config[0]==PHYFOB_CONN_PARAMTER){
		//
		custom_param.interval_min = phyfob_config.config[1];
		custom_param.interval_max = phyfob_config.config[2];
		custom_param.latency = phyfob_config.config[3];
		custom_param.timeout = phyfob_config.config[4];
		printk("min %i, max %i, latency %i, timeout %i \r\n",custom_param.interval_min,custom_param.interval_max,custom_param.latency,custom_param.timeout);
		
		last_connection = conn;
		k_work_schedule(&my_delayed_work, K_MSEC(200));
	}else if (phyfob_config.config[0]==PHYFOB_CUSTOM_NAME)
	{
		uint32_t my_int32_value;
		memcpy(&my_int32_value, &phyfob_config.config[1], 4);
		uicr_update_customer(my_int32_value);
		k_msleep(100);

		NVIC_SystemReset();
	}

	return 0;
}

extern void send_data(uint8_t ID, float* DATA,uint8_t LEN){

	if(event_data.RUNNING){
		if(ID == SENSOR_BMP581_ID){
			bt_gatt_notify_uuid(NULL, &bmp_uuid.uuid,&phyphox_gatt.attrs[0],DATA,LEN);
			return;
		}
		if(ID == SENSOR_LSM6DSR_ACC_ID){
			bt_gatt_notify(NULL,attr_lsm_acc,DATA,LEN);
			return;
		}
		if(ID == SENSOR_LSM6DSR_GYR_ID){
			bt_gatt_notify(NULL,attr_lsm_gyr,DATA,LEN);
			return;
		}
		if(ID == SENSOR_HDC_ID){
			bt_gatt_notify_uuid(NULL, &hdc_uuid.uuid,&phyphox_gatt.attrs[0],DATA,LEN);
			return;
		}
		if(ID == SENSOR_STCC4_ID){
			bt_gatt_notify_uuid(NULL, &stcc4_uuid.uuid,&phyphox_gatt.attrs[0],DATA,LEN);
			return;
		}
		if(ID == SENSOR_DATALOG_ID){
			bt_gatt_notify_uuid(NULL, &datalog_uuid.uuid,&phyphox_gatt.attrs[0],DATA,LEN);
			return;
		}
	}
};