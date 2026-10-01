#include "datalog.h"

#include "sensors.h"
#include "ble.h"
#include "bmpZephyr.h"

#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/crc.h>
#include <pm_config.h>
#include <stddef.h>
#include <string.h>

#define DATALOG_SLOT_SIZE   32
#define DATALOG_SECTOR_SIZE 4096

/* The last sector of the datalog partition holds the user's logging
 * settings so that they survive a reboot - and, more to the point, a
 * battery change: the sensor selection and the interval must not have to
 * be entered in the app again every time the cell is swapped.
 *
 * Carving the sector out of the existing partition (instead of adding a
 * new one) deliberately leaves the flash layout untouched, so the address
 * MCUboot expects its secondary slot at - baked into a bootloader that can
 * only be replaced over SWD - does not move.
 *
 * Settings are appended slot by slot within that sector, which is erased
 * only once it is full, so the (rare) configuration writes spread out over
 * 256 slots instead of erasing the same sector on every change. */
#define DATALOG_CFG_SIZE      DATALOG_SECTOR_SIZE
#define DATALOG_CFG_SLOT_SIZE 16
#define DATALOG_CFG_SLOTS     (DATALOG_CFG_SIZE / DATALOG_CFG_SLOT_SIZE)
#define DATALOG_CFG_MAGIC     0x4746434dUL /* "MCFG" */
#define DATALOG_CFG_VERSION   1

struct datalog_cfg {
    uint32_t magic;
    uint8_t version;
    uint8_t mask;
    uint16_t interval_s;
    uint8_t running;
    uint8_t bthome;
    uint8_t reserved[2];
    /* CRC over everything before it, so a record torn apart by a battery
     * that dies mid-write is rejected instead of restored as garbage. */
    uint32_t crc;
};
BUILD_ASSERT(sizeof(struct datalog_cfg) == DATALOG_CFG_SLOT_SIZE,
             "datalog_cfg must fill exactly one config slot");

static const struct flash_area *datalog_fa;
static uint32_t ring_size;
static uint32_t write_offset;
static uint8_t active_mask;
static uint8_t active_record_size;
static uint16_t active_interval_s;
static bool active_running;
static bool active_bthome;

/* Offset of the config sector inside the partition, index of the next
 * unwritten slot in it, and a mirror of the record currently stored
 * there (magic stays 0 until anything has been written). */
static uint32_t cfg_base;
static uint32_t cfg_next_slot;
static struct datalog_cfg stored_cfg;

static struct k_timer timer_datalog;
static struct k_work work_datalog;
static bool dump_in_progress;

static uint8_t datalog_record_size(uint8_t mask)
{
    uint8_t fields = 0;
    for (uint8_t bit = 0; bit < 4; bit++) {
        if (mask & (1 << bit)) {
            fields++;
        }
    }
    return 4 + 4 * fields;
}

static uint32_t datalog_cfg_crc(const struct datalog_cfg *cfg)
{
    return crc32_ieee((const uint8_t *)cfg, offsetof(struct datalog_cfg, crc));
}

/* Scans the config sector for the newest valid record. Records are only
 * ever appended, so the last valid one before the first erased slot is the
 * current configuration. Returns true if one was found. */
static bool datalog_cfg_load(void)
{
    struct datalog_cfg slot;
    bool found = false;

    cfg_next_slot = DATALOG_CFG_SLOTS;

    for (uint32_t i = 0; i < DATALOG_CFG_SLOTS; i++) {
        int err = flash_area_read(datalog_fa, cfg_base + i * DATALOG_CFG_SLOT_SIZE,
                                  &slot, sizeof(slot));
        if (err) {
            printk("datalog: config read failed at slot %u (%d)\r\n", i, err);
            cfg_next_slot = i;
            break;
        }
        if (slot.magic == 0xFFFFFFFF) {
            cfg_next_slot = i;
            break;
        }
        if (slot.magic == DATALOG_CFG_MAGIC && slot.version == DATALOG_CFG_VERSION &&
            slot.crc == datalog_cfg_crc(&slot)) {
            stored_cfg = slot;
            found = true;
        } else {
            printk("datalog: ignoring invalid config record in slot %u\r\n", i);
        }
    }

    return found;
}

/* Persists the settings, unless flash already holds exactly these values -
 * every start/stop command would otherwise burn a slot for nothing. */
static void datalog_cfg_save(uint8_t mask, uint16_t interval_s, bool running, bool bthome)
{
    struct datalog_cfg cfg = {
        .magic = DATALOG_CFG_MAGIC,
        .version = DATALOG_CFG_VERSION,
        .mask = mask,
        .interval_s = interval_s,
        .running = running ? 1 : 0,
        .bthome = bthome ? 1 : 0,
    };
    cfg.crc = datalog_cfg_crc(&cfg);

    if (memcmp(&cfg, &stored_cfg, sizeof(cfg)) == 0) {
        return;
    }

    if (cfg_next_slot >= DATALOG_CFG_SLOTS) {
        printk("datalog: config sector full, erasing\r\n");
        int erase_err = flash_area_erase(datalog_fa, cfg_base, DATALOG_CFG_SIZE);
        if (erase_err) {
            printk("datalog: config erase failed (%d)\r\n", erase_err);
            return;
        }
        cfg_next_slot = 0;
    }

    int err = flash_area_write(datalog_fa, cfg_base + cfg_next_slot * DATALOG_CFG_SLOT_SIZE,
                               &cfg, sizeof(cfg));
    if (err) {
        printk("datalog: config write failed (%d)\r\n", err);
        return;
    }

    stored_cfg = cfg;
    cfg_next_slot++;
    printk("datalog: config saved to slot %u (mask=0x%02x interval=%us running=%u bthome=%u)\r\n",
           cfg_next_slot - 1, cfg.mask, cfg.interval_s, cfg.running, cfg.bthome);
}

void datalog_erase(void)
{
    printk("datalog: erasing ring buffer (%u bytes)\r\n", ring_size);
    flash_area_erase(datalog_fa, 0, ring_size);
    write_offset = 0;
}

static void datalog_tick(struct k_work *work)
{
    /* Pause background logging while any phone is connected: BMP/HDC/STCC4
     * share their power-mode state with the live-view experiments, so
     * logging and live streaming would otherwise fight over the same
     * sensor resources. */
    if (!logging.enable || active_mask == 0) {
        printk("datalog: tick skipped (logging.enable=%d, mask=0x%02x)\r\n",
               logging.enable, active_mask);
        return;
    }
    if (dump_in_progress) {
        printk("datalog: tick skipped, dump in progress\r\n");
        return;
    }

    /* Trigger each selected sensor's measurement right here, synchronously,
     * instead of reading whatever a separately-timed background sample
     * last cached - that could be up to a full interval stale depending on
     * the (uncontrolled) phase between the two timers. */
    float co2 = 0, temperature = 0, humidity = 0, pressure = 0;

    if (active_mask & DATALOG_SENSOR_CO2) {
        stcc4_read_once(&co2);
    }
    if (active_mask & (DATALOG_SENSOR_TEMP | DATALOG_SENSOR_HUMIDITY)) {
        hdc_read_once(&temperature, &humidity);
    }
    if (active_mask & DATALOG_SENSOR_PRESSURE) {
        bmp_read_once(&pressure, NULL);
    }

    uint8_t buf[DATALOG_SLOT_SIZE];
    uint32_t ts = (uint32_t)(k_uptime_get() / 1000);
    uint8_t pos = 0;

    memcpy(&buf[pos], &ts, 4);
    pos += 4;

    if (active_mask & DATALOG_SENSOR_CO2) {
        memcpy(&buf[pos], &co2, 4);
        pos += 4;
    }
    if (active_mask & DATALOG_SENSOR_TEMP) {
        memcpy(&buf[pos], &temperature, 4);
        pos += 4;
    }
    if (active_mask & DATALOG_SENSOR_HUMIDITY) {
        memcpy(&buf[pos], &humidity, 4);
        pos += 4;
    }
    if (active_mask & DATALOG_SENSOR_PRESSURE) {
        memcpy(&buf[pos], &pressure, 4);
        pos += 4;
    }

    if (write_offset % DATALOG_SECTOR_SIZE == 0) {
        printk("datalog: erasing sector at offset %u\r\n", write_offset);
        flash_area_erase(datalog_fa, write_offset, DATALOG_SECTOR_SIZE);
    }
    flash_area_write(datalog_fa, write_offset, buf, pos);
    printk("datalog: wrote record @%u ts=%u co2=%f t=%f rh=%f p=%f\r\n",
           write_offset, ts, co2, temperature, humidity, pressure);

    write_offset += DATALOG_SLOT_SIZE;
    if (write_offset >= ring_size) {
        printk("datalog: ring buffer wrapped around\r\n");
        write_offset = 0;
    }

    /* Push the same values out over BTHome so receivers that never connect
     * (Home Assistant and friends) see them. This can only ever run while no
     * phone is connected, because logging.enable is false for the duration
     * of a connection - see the guard at the top of this function. */
    bthome_publish(active_mask, co2, temperature, humidity, pressure);
}

static void timer_datalog_handler(struct k_timer *timer)
{
    k_work_submit(&work_datalog);
}

static void datalog_dump(uint16_t max_age_minutes)
{
    if (active_mask == 0) {
        printk("datalog: dump requested but no active session (mask=0), nothing to send\r\n");
        return;
    }

    uint32_t total_slots = ring_size / DATALOG_SLOT_SIZE;
    uint32_t last_slot_offset = (total_slots - 1) * DATALOG_SLOT_SIZE;
    uint32_t ts;

    flash_area_read(datalog_fa, last_slot_offset, &ts, 4);
    bool wrapped = (ts != 0xFFFFFFFF);

    uint32_t start_slot = wrapped ? (write_offset / DATALOG_SLOT_SIZE) : 0;
    uint32_t count = wrapped ? total_slots : (write_offset / DATALOG_SLOT_SIZE);

    /* max_age_minutes == 0 means "no limit, dump everything". Otherwise
     * only records whose raw (boot-relative) timestamp is within the last
     * max_age_minutes get sent - the rest are still read (records aren't
     * indexed by time) but skipped before packing them into a packet. */
    bool filter_by_age = (max_age_minutes > 0);
    uint32_t cutoff_ts = 0;
    if (filter_by_age) {
        uint32_t now = (uint32_t)(k_uptime_get() / 1000);
        uint32_t max_age_s = (uint32_t)max_age_minutes * 60;
        cutoff_ts = (max_age_s < now) ? (now - max_age_s) : 0;
    }

    printk("datalog: dumping up to %u records (wrapped=%d, record_size=%u, start_slot=%u, max_age=%u min)\r\n",
           count, wrapped, active_record_size, start_slot, max_age_minutes);

    dump_in_progress = true;

    uint8_t packet[180];
    uint16_t packet_pos = 0;
    uint8_t record[DATALOG_SLOT_SIZE];
    uint32_t sent = 0;

    for (uint32_t i = 0; i < count; i++) {
        uint32_t slot = (start_slot + i) % total_slots;
        flash_area_read(datalog_fa, slot * DATALOG_SLOT_SIZE, record, active_record_size);

        /* Stored timestamps are raw "seconds since boot" (monotonic within
         * a boot, which is all that matters since the ring is erased on
         * every boot - see init_datalog()). Convert to "seconds since the
         * phyphox experiment was started" here, at dump time, matching how
         * every other sensor stream in this project reports time - global_
         * timestamp is only meaningful for the currently running session,
         * not while a record was written in the background. */
        uint32_t raw_ts;
        memcpy(&raw_ts, record, 4);

        if (filter_by_age && raw_ts < cutoff_ts) {
            continue;
        }

        /* Signed int, not float: raw_ts only ever has whole-second
         * resolution, so float's fractional precision buys nothing here,
         * while float32's 24-bit mantissa would start rounding whole
         * seconds once raw_ts exceeds ~194 days of uptime. int32 stays
         * exact up to ~68 years. */
        int32_t rel_ts = (int32_t)raw_ts - (int32_t)global_timestamp;
        memcpy(record, &rel_ts, 4);

        if (packet_pos + active_record_size > sizeof(packet)) {
            send_data(SENSOR_DATALOG_ID, (float *)packet, packet_pos);
            k_sleep(K_MSEC(100));
            packet_pos = 0;
        }
        memcpy(&packet[packet_pos], record, active_record_size);
        packet_pos += active_record_size;
        sent++;
    }
    if (packet_pos > 0) {
        send_data(SENSOR_DATALOG_ID, (float *)packet, packet_pos);
    }
    dump_in_progress = false;
    printk("datalog: dump complete, sent %u of %u records\r\n", sent, count);
}

void datalog_get_state(struct datalog_state *state)
{
    state->running = active_running;
    state->mask = active_mask;
    state->interval_s = active_interval_s;
    state->bthome = active_bthome;
}

void datalog_configure(uint8_t cmd, uint8_t sensors_mask, uint16_t interval_s)
{
    printk("CMD: %i\r\n",cmd);
    switch (cmd) {
        case DATALOG_CMD_START:
            printk("START DATALOGGING CMD: mask=0x%02x interval=%us (previous mask=0x%02x)\r\n",
                   sensors_mask, interval_s, active_mask);
            if (sensors_mask != active_mask) {
                printk("datalog: sensor mask changed, erasing previous log\r\n");
                datalog_erase();
                active_mask = sensors_mask;
                active_record_size = datalog_record_size(active_mask);
            }
            if (interval_s == 0) {
                interval_s = 30;
            }
            active_interval_s = interval_s;
            printk("datalog: logging active, record_size=%u bytes, ring capacity=%u records\r\n",
                   active_record_size, ring_size / DATALOG_SLOT_SIZE);
            k_timer_start(&timer_datalog, K_SECONDS(interval_s), K_SECONDS(interval_s));
            active_running = true;
            datalog_cfg_save(active_mask, active_interval_s, active_running, active_bthome);
            break;
        case DATALOG_CMD_STOP:
            printk("STOP DATALOGGING CMD\r\n");
            k_timer_stop(&timer_datalog);
            active_running = false;
            datalog_cfg_save(active_mask, active_interval_s, active_running, active_bthome);
            break;
        case DATALOG_CMD_DUMP:
            /* Here, the "interval_s" parameter is reinterpreted as the
             * maximum age of records to send, in minutes (0 = no limit). */
            printk("DUMP DATALOGGING CMD, max_age=%u min\r\n", interval_s);
            datalog_dump(interval_s);
            break;
        case DATALOG_CMD_ERASE:
            printk("ERASE DATALOGGING CMD\r\n");
            datalog_erase();
            break;
        case DATALOG_CMD_BTHOME:
            /* Here, the "interval_s" parameter is reinterpreted once more:
             * 0 turns the BTHome advertising bursts off, anything else turns
             * them on. */
            printk("BTHOME ADVERTISING CMD: %s\r\n", interval_s ? "enable" : "disable");
            bthome_set_enabled(interval_s != 0);
            active_bthome = (interval_s != 0);
            datalog_cfg_save(active_mask, active_interval_s, active_running, active_bthome);
            break;
        default:
            break;
    }
}

bool init_datalog(void)
{
    int err = flash_area_open(PM_DATALOG_ID, &datalog_fa);
    if (err) {
        printk("datalog: failed to open flash area (%d)\r\n", err);
        return false;
    }

    if (datalog_fa->fa_size <= DATALOG_CFG_SIZE) {
        printk("datalog: flash area too small (%u bytes)\r\n", datalog_fa->fa_size);
        return false;
    }

    /* The tail sector is the settings store, everything before it is the
     * record ring - datalog_erase() therefore never touches the settings. */
    cfg_base = ROUND_DOWN(datalog_fa->fa_size - DATALOG_CFG_SIZE, DATALOG_SECTOR_SIZE);
    ring_size = cfg_base;
    printk("datalog: flash area opened, id=%d size=%u (ring %u, config @%u)\r\n",
           PM_DATALOG_ID, datalog_fa->fa_size, ring_size, cfg_base);

    write_offset = 0;
    active_mask = 0;
    active_record_size = 0;
    active_interval_s = 0;
    active_running = false;
    active_bthome = false;
    k_work_init(&work_datalog, datalog_tick);
    k_timer_init(&timer_datalog, timer_datalog_handler, NULL);

    /* Stored timestamps are seconds since boot, so data from a previous
     * boot would have overlapping/non-monotonic timestamps mixed in with
     * new records. Always start with a clean slate rather than resuming a
     * prior session. */
    datalog_erase();
    printk("datalog: ring buffer erased at boot\r\n");

    if (!datalog_cfg_load()) {
        printk("datalog: no stored configuration, waiting for start command\r\n");
        return false;
    }

    active_mask = stored_cfg.mask;
    active_record_size = datalog_record_size(active_mask);
    active_interval_s = stored_cfg.interval_s;
    active_bthome = (stored_cfg.bthome != 0);
    active_running = (stored_cfg.running != 0) && active_mask != 0 && active_interval_s != 0;

    /* Restored, not re-commanded: nothing here may write back to flash,
     * otherwise every single boot would consume a config slot. */
    bthome_set_enabled(active_bthome);

    if (active_running) {
        printk("datalog: restored session, mask=0x%02x interval=%us bthome=%u\r\n",
               active_mask, active_interval_s, active_bthome);
        k_timer_start(&timer_datalog, K_SECONDS(active_interval_s),
                      K_SECONDS(active_interval_s));
    } else {
        printk("datalog: restored settings, logging off (mask=0x%02x interval=%us bthome=%u)\r\n",
               active_mask, active_interval_s, active_bthome);
    }

    return true;
}
