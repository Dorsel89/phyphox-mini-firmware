#include "hdc.h"
//HDC hdc_data;

extern bool init_hdc()
{
    /* Register the work items and the timer before anything can fail.
     * They only store function pointers, no hardware is touched. If the
     * sensor does not answer we still leave this function early, but a
     * later configuration write then finds an initialised work item
     * instead of queueing a NULL handler and faulting the whole fob. */
    k_work_init(&work_hdc, send_data_hdc);
	k_work_init(&config_work_hdc, set_config_hdc);
    k_timer_init(&timer_hdc, hdc_data_ready, NULL);

    if(!device_is_ready(hdc_dev)){
        printk("Device not ready or not found");
        return false;
    }
    /* The datalog module now triggers every sensor's measurement directly
     * (see datalog_tick()) at its own configured interval, so this legacy
     * independently-timed background timer must stay stopped - starting it
     * here (as sleep_hdc(!logging.enable) used to, since logging.enable is
     * true at boot) caused a redundant real sensor read every 60s on top of
     * the datalog interval. */
    sleep_hdc(true);

    return true;
}

extern void sleep_hdc(bool sleep) 
{
    if (sleep) {
        k_timer_stop(&timer_hdc);
    }
    else{
        k_timer_start(&timer_hdc, K_MSEC(hdc_data.timer_interval), K_MSEC(hdc_data.timer_interval));
    }
}

/* Synchronous single-shot read for the datalog module: sensor_sample_fetch()
 * already blocks until the measurement is ready, so this is called directly
 * at datalog-tick time instead of relying on hdc's own independently-timed
 * background sample, which could be up to a full interval stale. */
void hdc_read_once(float *temperature, float *humidity)
{
    sensor_sample_fetch(hdc_dev);
    sensor_channel_get(hdc_dev, SENSOR_CHAN_AMBIENT_TEMP, &hdc_temp);
    sensor_channel_get(hdc_dev, SENSOR_CHAN_HUMIDITY, &hdc_humid);

    hdc_data.temperature = sensor_value_to_float(&hdc_temp);
    hdc_data.humidity = sensor_value_to_float(&hdc_humid);

    printk("hdc: datalog read temperature=%f C humidity=%f %%\r\n",
           hdc_data.temperature, hdc_data.humidity);
    if(temperature){
        *temperature = hdc_data.temperature;
    }
    if(humidity){
        *humidity = hdc_data.humidity;
    }
}

void hdc_data_ready()
{
    hdc_data.timestamp = k_uptime_ticks();
	k_work_submit(&work_hdc);
}

void send_data_hdc(struct k_work *work)
{
    sensor_sample_fetch(hdc_dev);
    sensor_channel_get(hdc_dev, SENSOR_CHAN_AMBIENT_TEMP, &hdc_temp);
    sensor_channel_get(hdc_dev, SENSOR_CHAN_HUMIDITY, &hdc_humid);
    
    hdc_data.temperature = sensor_value_to_float(&hdc_temp);
    hdc_data.humidity = sensor_value_to_float(&hdc_humid);

    if(logging.enable){
        printk("hdc: new reading temperature=%f C humidity=%f %%\r\n",
               hdc_data.temperature, hdc_data.humidity);
        stcc4_compensate(hdc_data.temperature,hdc_data.humidity);
        return;
    }

    hdc_data.array[0] = hdc_data.temperature;
    hdc_data.array[1] = hdc_data.humidity;
    hdc_data.array[2] = (hdc_data.timestamp/32768.0)-global_timestamp;

    
    send_data(SENSOR_HDC_ID, &hdc_data.array, 4*3);
}

void set_config_hdc(struct k_work *work)
{
    sleep_hdc(true);
    hdc_data.timer_interval = hdc_data.config[1]*100;
    printk("hdc config received \n");
    printk("hdc interval: %i\n",hdc_data.timer_interval);
    //Ensure minimum of 30ms
    if (hdc_data.timer_interval < 300) {hdc_data.timer_interval = 300;}
    sleep_hdc(!hdc_data.config[0]);
}

extern void submit_config_hdc()
{	
    k_work_submit(&config_work_hdc);
}