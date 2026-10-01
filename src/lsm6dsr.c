#include "lsm6dsr.h"
#include "ble.h"

static const struct gpio_dt_spec lsmInt = GPIO_DT_SPEC_GET_OR(LSM_INT, gpios,{0});
static struct gpio_callback lsmInt_cb_data;

#define SPIOP	SPI_WORD_SET(8) | SPI_TRANSFER_MSB

struct spi_dt_spec spispec = SPI_DT_SPEC_GET(DT_NODELABEL(lsm6dsr), SPIOP, 0);


static int16_t data_raw_acceleration[3];
static int16_t data_raw_angular_rate[3];
static uint8_t whoamI, rst;
stmdev_ctx_t dev_ctx;

static void lsmDataReady(const struct device *dev, struct gpio_callback *cb,uint32_t pins)
{
    lsm_data.timestamp = k_uptime_ticks();
	k_work_submit(&work_lsm);
}

uint8_t get_bit(uint8_t value, uint8_t n) {
    return (value >> n) & 1;
}

extern void adjust_lsm_configuration(struct k_work *work){
    //enable, rate, range acc, range gyr, format, eventsize, average
    enable_lsm(false);
    pm_device_action_run(spispec.bus, PM_DEVICE_ACTION_RESUME);
    if(DEBUG){printk("new config:\n\r");};
    if(DEBUG){printk("0 enable: %i\n\r",*lsm_en);};
    if(DEBUG){printk("1 rate: %i\n\r", *lsm_rate);};
    if(DEBUG){printk("2 range acc: %i\n\r",*lsm_range_acc);};
    if(DEBUG){printk("3 range gyr: %i\n\r",*lsm_range_gyr);};
    if(DEBUG){printk("4 format: %i\n\r",*lsm_format);};
    if(DEBUG){printk("5 event size: %i\n\r",*lsm_event_size);};
    
    lsm_data.event_number = 0;
    k_sleep(K_MSEC(100));

    //set ranges
    lsm6dsr_xl_full_scale_set(&dev_ctx, *lsm_range_acc);
    lsm6dsr_gy_full_scale_set(&dev_ctx, *lsm_range_gyr);
    
    //set eventsize
    lsm_data.event_size = *lsm_event_size;
    
    //enable and rates
    enable_lsm(*lsm_en);
    
}

float get_gyr_si(int16_t lsb){
    if(*lsm_range_gyr == LSM6DSR_125dps){
        return lsm6dsr_from_fs125dps_to_mdps(lsb)/1000.0;
    }else if(*lsm_range_gyr == LSM6DSR_250dps){
        return lsm6dsr_from_fs250dps_to_mdps(lsb)/1000.0;
    }else if(*lsm_range_gyr == LSM6DSR_500dps){
        return lsm6dsr_from_fs500dps_to_mdps(lsb)/1000.0;
    }else if(*lsm_range_gyr == LSM6DSR_1000dps){
        return lsm6dsr_from_fs1000dps_to_mdps(lsb)/1000.0;
    }else if(*lsm_range_gyr == LSM6DSR_2000dps){
        return lsm6dsr_from_fs2000dps_to_mdps(lsb)/1000.0;
    }else if(*lsm_range_gyr == LSM6DSR_4000dps){
        return lsm6dsr_from_fs4000dps_to_mdps(lsb)/1000.0;
    }
    return 0.0f;
}
/* Acceleration in m/s^2 for every range. The float packet builder uses
 * this as is - it used to multiply by 9.81 a second time, which made the
 * 2/4/8 g values 9.81 times too large while 16 g (converted to g only)
 * happened to come out right. */
float get_acc_si(int16_t lsb){
    if(*lsm_range_acc == LSM6DSR_2g){
        return lsm6dsr_from_fs2g_to_mg(lsb)*9.81/1000.0;
    }else if(*lsm_range_acc == LSM6DSR_4g){
        return lsm6dsr_from_fs4g_to_mg(lsb)*9.81/1000.0;
    }else if(*lsm_range_acc == LSM6DSR_8g){
        return lsm6dsr_from_fs8g_to_mg(lsb)*9.81/1000.0;
    }else if(*lsm_range_acc == LSM6DSR_16g){
        return lsm6dsr_from_fs16g_to_mg(lsb)*9.81/1000.0;
    }
    return 0.0f;
}
extern void send_data_lsm(struct k_work *work){
       
    if(get_bit(*lsm_en,ACC_BIT)){
        memset(data_raw_acceleration, 0x00, 3 * sizeof(int16_t));
        lsm6dsr_acceleration_raw_get(&dev_ctx, data_raw_acceleration);
        memcpy(&lsm_data.acc_array[lsm_data.event_number*3+0+2],&data_raw_acceleration[0],2*3);
        
        if(lsm_data.event_number==0 || *lsm_format == FORMAT_FLOAT){
            lsm_data.acc_time[lsm_data.event_number]=(lsm_data.timestamp/32768.0)-global_timestamp;
        }
    }
    if(get_bit(*lsm_en,GYR_BIT)){
        memset(data_raw_angular_rate, 0x00, 3 * sizeof(int16_t));
        lsm6dsr_angular_rate_raw_get(&dev_ctx, data_raw_angular_rate);
        memcpy(&lsm_data.gyr_array[lsm_data.event_number*3+0+2],&data_raw_angular_rate[0],2*3);
        
        if(lsm_data.event_number==0 || *lsm_format == FORMAT_FLOAT){
            lsm_data.gyr_time[lsm_data.event_number]=(lsm_data.timestamp/32768.0)-global_timestamp;
        }
    }    
    lsm_data.event_number++;

    uint8_t reset = 0;
    //ACCELEROMETER
    if(get_bit(*lsm_en,ACC_BIT)){
        //FORMAT INT16
        if(lsm_data.event_number == lsm_data.event_size && *lsm_format == FORMAT_INT16){
            memcpy(&lsm_data.acc_array[0],&lsm_data.acc_time[0],4);
            reset+=1;
            send_data(SENSOR_LSM6DSR_ACC_ID, &lsm_data.acc_array[0], lsm_data.event_size*6+4);           
        }else if(lsm_data.event_number == lsm_data.event_size && *lsm_format == FORMAT_FLOAT){
            float data_float_format[15*4];
            for(int i = 0; i<lsm_data.event_number;i++){
                data_float_format[i*4] = lsm_data.acc_time[i];
                
                data_float_format[i*4+1] = get_acc_si(lsm_data.acc_array[i*3+0+2]);
                data_float_format[i*4+2] = get_acc_si(lsm_data.acc_array[i*3+1+2]);
                data_float_format[i*4+3] = get_acc_si(lsm_data.acc_array[i*3+2+2]);
            }
            reset+=1;
            /* event_size*16, not a fixed 240: a constant length made every
             * packet look like 15 samples, so anything the loop had not
             * filled was decoded by the client as real measurements. */
            send_data(SENSOR_LSM6DSR_ACC_ID, &data_float_format[0], lsm_data.event_size*16);           
        }
    }
    if(get_bit(*lsm_en,GYR_BIT)){
        //FORMAT INT16
        if(lsm_data.event_number == lsm_data.event_size && *lsm_format == FORMAT_INT16){
            memcpy(&lsm_data.gyr_array[0],&lsm_data.gyr_time[0],4);
            reset+=1;
            send_data(SENSOR_LSM6DSR_GYR_ID, &lsm_data.gyr_array[0], lsm_data.event_size*6+4);           

        }else if(lsm_data.event_number == lsm_data.event_size && *lsm_format == FORMAT_FLOAT){
            float data_float_format[15*4];
            for(int i = 0; i<lsm_data.event_number;i++){
                data_float_format[i*4] = lsm_data.gyr_time[i];                
                data_float_format[i*4+1] = get_gyr_si(lsm_data.gyr_array[i*3+0+2]);
                data_float_format[i*4+2] = get_gyr_si(lsm_data.gyr_array[i*3+1+2]);
                data_float_format[i*4+3] = get_gyr_si(lsm_data.gyr_array[i*3+2+2]);
            }
            reset+=1;
            /* event_size*16, not a fixed 240: a constant length made every
             * packet look like 15 samples, so anything the loop had not
             * filled was decoded by the client as real measurements. */
            send_data(SENSOR_LSM6DSR_GYR_ID, &data_float_format[0], lsm_data.event_size*16);           
        }
    }
    if(reset){
        lsm_data.event_number=0;
    }
}

int8_t configure_int(){
    k_work_init(&work_lsm, send_data_lsm);
    k_work_init(&config_work_lsm, adjust_lsm_configuration);
    uint8_t lsmResult;
    lsmResult = gpio_pin_configure_dt(&lsmInt, GPIO_INPUT);
    if (lsmResult != 0) {
		printk("Error %d: failed to configure %s pin %d\n\r",
		       lsmResult, lsmInt.port->name, lsmInt.pin);
		return lsmResult;
	}
    lsmResult = gpio_pin_interrupt_configure_dt(&lsmInt,GPIO_INT_EDGE_RISING);
	if (lsmResult != 0) {
		printk("Error %d: failed to configure interrupt on %s pin %d\n\r",
			lsmResult, lsmInt.port->name, lsmInt.pin);
		return lsmResult;
	}
    gpio_init_callback(&lsmInt_cb_data, lsmDataReady, BIT(lsmInt.pin));
    gpio_add_callback(lsmInt.port, &lsmInt_cb_data);

    return 0;
}


static int32_t platform_write(void *handle, uint8_t reg, const uint8_t *bufp,
                              uint16_t len){

    uint8_t address;
	struct spi_buf transmit_buffers[2];
	struct spi_buf_set transmit_buffer_set;
	int ret;

	address = reg;

	transmit_buffers[0].buf = &address;
	transmit_buffers[0].len = 1;
	transmit_buffers[1].buf = (uint8_t *)bufp;
	transmit_buffers[1].len = len;


	transmit_buffer_set.buffers = transmit_buffers;
	transmit_buffer_set.count = 2;

    ret = spi_write_dt(&spispec, &transmit_buffer_set);
    
	k_usleep(2);

    return 0;
    }

static int32_t platform_read(void *handle, uint8_t reg, uint8_t *bufp,
                             uint16_t len){

    int ret;

    uint8_t address[1];
	struct spi_buf transmit_buffer;
	struct spi_buf_set transmit_buffer_set;
	struct spi_buf receive_buffers[2];
	struct spi_buf_set receive_buffers_set;

	address[0] = (0x80 | reg);//set first bit to 1 for read mode!

	transmit_buffer.buf = address;
	transmit_buffer.len = sizeof(address);

	transmit_buffer_set.buffers = &transmit_buffer;
	transmit_buffer_set.count = 1;

	receive_buffers[0].buf = NULL;
	receive_buffers[0].len = 1;
	receive_buffers[1].buf = bufp;
	receive_buffers[1].len = len;

	receive_buffers_set.buffers = receive_buffers;
	receive_buffers_set.count = 2;

	ret = spi_transceive_dt(&spispec, &transmit_buffer_set, &receive_buffers_set);
    if (ret < 0) {
		printk("spi_transceive_dt() failed, err: %d", ret);
		return ret;
	}

	return ret;
}

static void platform_delay(uint32_t ms)
{
    k_msleep(ms);
}

/* The data-ready interrupt drives the whole readout, so it has to come from
 * a sensor that is running. With the accelerometer off its DRDY never fires
 * and gyro-only produced no data at all. With both on they share the ODR and
 * send_data_lsm() reads both per interrupt, so one source is enough - INT1
 * ORs the routed signals, and two near-simultaneous pulses would read some
 * samples twice. */
static void route_drdy(uint8_t en){
    lsm6dsr_pin_int1_route_t route;
    lsm6dsr_pin_int1_route_get(&dev_ctx, &route);
    route.int1_ctrl.int1_drdy_xl = get_bit(en, ACC_BIT);
    route.int1_ctrl.int1_drdy_g = !get_bit(en, ACC_BIT) && get_bit(en, GYR_BIT);
    lsm6dsr_pin_int1_route_set(&dev_ctx, &route);
}

uint8_t enable_lsm(uint8_t en){
    pm_device_action_run(spispec.bus, PM_DEVICE_ACTION_RESUME);
    if(en == 0){
        lsm6dsr_xl_data_rate_set(&dev_ctx, LSM6DSR_XL_ODR_OFF);
        lsm6dsr_gy_data_rate_set(&dev_ctx, LSM6DSR_GY_ODR_OFF);
        pm_device_action_run(spispec.bus,PM_DEVICE_ACTION_SUSPEND);
        lsm_data.event_number = 0;
    }else{
        route_drdy(en);
        if(get_bit(en,ACC_BIT)){
            printk("set acc rate to %i \r\n",*lsm_rate);
            lsm6dsr_xl_data_rate_set(&dev_ctx, *lsm_rate);
        }else{
            lsm6dsr_xl_data_rate_set(&dev_ctx, LSM6DSR_XL_ODR_OFF);
        }
        if(get_bit(en,GYR_BIT)){
            lsm6dsr_gy_data_rate_set(&dev_ctx, *lsm_rate);
        }else{
            lsm6dsr_gy_data_rate_set(&dev_ctx, LSM6DSR_GY_ODR_OFF);
        }
    }
    return 0;
}

int8_t init_lsm(){
    printk("init function\r\n");
    
    dev_ctx.write_reg = platform_write;
    dev_ctx.read_reg = platform_read;
    dev_ctx.mdelay = platform_delay;
    //dev_ctx.handle = &SENSOR_BUS; //needed?

    lsm_data.event_number = 0;
    lsm_data.event_size = 40;

    lsm_en = &lsm_data.config[0];
    lsm_rate = &lsm_data.config[1];
    lsm_range_acc = &lsm_data.config[2];
    lsm_range_gyr = &lsm_data.config[3];
    lsm_format = &lsm_data.config[4];    
    lsm_event_size = &lsm_data.config[5];


    lsm6dsr_device_id_get(&dev_ctx, &whoamI);
    if (whoamI != LSM6DSR_ID){
        printk("LSM6DSR_ID not found. I got the id: %i \r\n", whoamI);
        return -1;
    }
    lsm6dsr_reset_set(&dev_ctx, PROPERTY_ENABLE);
    do {
    lsm6dsr_reset_get(&dev_ctx, &rst);
    } while (rst);

    configure_int();
    lsm6dsr_data_ready_mode_set(&dev_ctx,LSM6DSR_DRDY_PULSED);

    /* INT1 routing follows the enabled sensors, see route_drdy() */

    //int1_route.md1_cfg.int1_double_tap = PROPERTY_ENABLE;
    /* Disable I3C interface */
    lsm6dsr_i3c_disable_set(&dev_ctx, LSM6DSR_I3C_DISABLE);
    /* Enable Block Data Update */
    lsm6dsr_block_data_update_set(&dev_ctx, PROPERTY_DISABLE);
    /* Set Output Data Rate */
    lsm6dsr_xl_data_rate_set(&dev_ctx, LSM6DSR_XL_ODR_OFF);
    lsm6dsr_gy_data_rate_set(&dev_ctx, LSM6DSR_GY_ODR_OFF);
    /* Set full scale */
    lsm6dsr_xl_full_scale_set(&dev_ctx, LSM6DSR_4g);
    lsm6dsr_gy_full_scale_set(&dev_ctx, LSM6DSR_2000dps);
    /* Configure filtering chain(No aux interface)
    * Accelerometer - LPF1 + LPF2 path
    */
    //lsm6dsr_xl_hp_path_on_out_set(&dev_ctx, LSM6DSR_LP_ODR_DIV_100);
    lsm6dsr_xl_hp_path_on_out_set(&dev_ctx, LSM6DSR_HP_PATH_DISABLE_ON_OUT);
    
    lsm6dsr_xl_filter_lp2_set(&dev_ctx, PROPERTY_DISABLE);

    enable_lsm(0);

    return 0;
}