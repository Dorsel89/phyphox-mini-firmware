#include "stcc4Zephyr.h"
#include "ble.h"
void submit_config_stcc4(){
    k_work_submit(&config_work_stcc4);
}

static void calibration_work(struct k_work *work)
{
    int16_t my_frc_correction;
    stcc4_stop_continuous_measurement();
    k_sleep(K_MSEC(1200));
    stcc4_perform_forced_recalibration(10*stcc4_data.config[1],&my_frc_correction);
    if(my_frc_correction == 0xFFFF){
        printk("STCC4 Calibration failed\r\n");
    }else{
        printk("STCC4 Calibrated\r\n");
    }
}
K_WORK_DELAYABLE_DEFINE(stcc4_calibration_work, calibration_work);

/* Continuous mode delivers one result per second */
#define STCC4_MIN_INTERVAL_MS 1000

void set_config_stcc4(struct k_work *work)
{
    sleep_stcc4(true);

    //CALIBRATION
    if (stcc4_data.config[0]== STCC4_CALIBRATION)
    {
        printk("start stcc4 calibration \r\n");
        //measure for 30s
        stcc4_data.timer_interval = STCC4_MIN_INTERVAL_MS;
        sleep_stcc4(false);
        k_work_schedule(&stcc4_calibration_work,K_SECONDS(30));
        return;
    }
    
    stcc4_data.timer_interval = stcc4_data.config[1]*100;
    printk("stcc4 config received \n");
    printk("stcc4 interval: %i\n",stcc4_data.timer_interval);
    /* The sensor produces one result per second in continuous mode. Reading
     * faster only returns "no new data" errors, which used to go out as 0 ppm. */
    if (stcc4_data.timer_interval < STCC4_MIN_INTERVAL_MS) {
        stcc4_data.timer_interval = STCC4_MIN_INTERVAL_MS;
    }
    sleep_stcc4(!stcc4_data.config[0]);
}

extern int8_t init_stcc4(){
    stcc4_data.enable = &stcc4_data.config[0];
    /* Register the work items and the timer before anything can fail.
     * They only store function pointers, no hardware is touched. If the
     * sensor does not answer we still leave this function early, but a
     * later configuration write then finds an initialised work item
     * instead of queueing a NULL handler and faulting the whole fob. */
    k_work_init(&work_stcc4, send_data_stcc4);
	k_work_init(&config_work_stcc4, set_config_stcc4);
    k_timer_init(&timer_stcc4, stcc4_data_ready, NULL);

    if(!device_is_ready(stcc4_dev)){
        printk("Device stcc4_dev not ready or not found");
        return false;
    }

    stcc4_init(STCC4_I2C_ADDR_64);

    int16_t error = NO_ERROR;
    sensirion_i2c_hal_init();
    
    error = stcc4_stop_continuous_measurement();
    int loop = 0;
    while(error){
        k_msleep(200);
        error = stcc4_stop_continuous_measurement();
        if(loop>=3){
            return error;
        }
        loop+=1;
    }
    
    if (error != NO_ERROR) {
            printk("error executing stop_continuous_measurement(): %i\n", error);
            return error;
    }

    /* The datalog module now triggers every sensor's measurement directly
     * (see datalog_tick()) at its own configured interval, so this legacy
     * independently-timed background timer must stay stopped - starting it
     * here (as sleep_stcc4(!logging.enable) used to, since logging.enable
     * is true at boot) caused a redundant real sensor read every 60s on top
     * of the datalog interval. */
    sleep_stcc4(true);
    return true;

}

extern uint8_t sleep_stcc4(bool SLEEP){
    if(SLEEP){
        k_timer_stop(&timer_stcc4);
        stcc4_stop_continuous_measurement();
        stcc4_enter_sleep_mode();
    }else{
        stcc4_exit_sleep_mode();
        if(logging.enable){
            stcc4_enter_sleep_mode();
        }else{
            stcc4_start_continuous_measurement();
        }
        k_timer_start(&timer_stcc4, K_MSEC(stcc4_data.timer_interval), K_MSEC(stcc4_data.timer_interval));
    }
    return 0;
}

extern uint8_t stcc4_compensate(float t, float rh){
    uint16_t t_u16 = (t+45)*(pow(2,16)-1)/175;
    uint16_t rh_u16 = (rh+6)*(pow(2,16)-1)/125;
    printk("compensation with: t: %i rh: %i \r\n",t_u16,rh_u16);
    if(logging.enable){
        stcc4_exit_sleep_mode();
        stcc4_set_rht_compensation(t_u16,rh_u16);
        stcc4_enter_sleep_mode();
    }

    return 0;
}

/* Synchronous single-shot read for the datalog module: the vendor driver
 * calls already block until the measurement is ready, so this is called
 * directly at datalog-tick time instead of relying on stcc4's own
 * independently-timed background sample, which could be up to a full
 * interval stale. */
void stcc4_read_once(float *co2)
{
    int16_t co2_concentration_raw = 0;
    uint16_t temperature_raw = 0;
    uint16_t relative_humidity_raw = 0;
    uint16_t sensor_status_raw = 0;

    stcc4_exit_sleep_mode();
    stcc4_measure_single_shot();
    stcc4_read_measurement_raw(
        &co2_concentration_raw, &temperature_raw, &relative_humidity_raw,
        &sensor_status_raw);
    stcc4_enter_sleep_mode();
    stcc4_data.co2 = co2_concentration_raw;

    printk("stcc4: datalog read co2=%d ppm\r\n", co2_concentration_raw);
    if(co2){
        *co2 = stcc4_data.co2;
    }
}

void send_data_stcc4(struct k_work *work)
{
    int16_t co2_concentration_raw = 0;
    uint16_t temperature_raw = 0;
    uint16_t relative_humidity_raw = 0;
    uint16_t sensor_status_raw = 0;

    
    if(logging.enable){
        //logging mode
        //sensor is not running - start, measure, sleep
        printk("stcc4 logging mode\r\n");
        stcc4_exit_sleep_mode();
        stcc4_measure_single_shot();
        stcc4_read_measurement_raw(
            &co2_concentration_raw, &temperature_raw, &relative_humidity_raw,
            &sensor_status_raw);
        stcc4_enter_sleep_mode();
        stcc4_data.co2 = co2_concentration_raw;
        printk("stcc4: new reading co2=%d ppm\r\n", co2_concentration_raw);
        return;

    }else{
        //phyphox live mode
        int16_t error = stcc4_read_measurement_raw(
            &co2_concentration_raw, &temperature_raw, &relative_humidity_raw,
            &sensor_status_raw);
        
        printk("stcc4 live mode \r\n");
        /* No result (not ready yet, or the read failed): send nothing rather
         * than a 0 ppm reading that looks like a real measurement. */
        if (error != NO_ERROR) {
            printk("stcc4: read failed (%i), nothing sent\r\n", error);
            return;
        }
    }
    stcc4_data.co2 = co2_concentration_raw;
    stcc4_data.array[0]=stcc4_data.co2;
    
    printk("send new co2 data: co: %i \r\n",co2_concentration_raw);

    send_data(SENSOR_STCC4_ID, &stcc4_data.array, 4*2);
}

void stcc4_data_ready()
{
    stcc4_data.array[1]=(k_uptime_ticks()/32768.0)-global_timestamp;
	k_work_submit(&work_stcc4);
}