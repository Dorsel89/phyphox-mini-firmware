#include "bas.h"

//static const struct adc_dt_spec adc_spec = ADC_DT_SPEC_GET_BY_IDX(DT_PATH(zephyr_user), 0);
//static int16_t sample_buffer;
static int16_t m_sample_buffer[BUFFER_SIZE];

static const struct adc_channel_cfg m_1st_channel_cfg = {
	.gain = ADC_GAIN,
	.reference = ADC_REFERENCE,
	.acquisition_time = ADC_ACQUISITION_TIME,
	.channel_id = ADC_1ST_CHANNEL_ID,
#if defined(CONFIG_ADC_CONFIGURABLE_INPUTS)
	.input_positive = NRF_SAADC_INPUT_VDD,
#endif
};

extern void init_BAS(){
	int err = 0;
	adc_dev = DEVICE_DT_GET(DT_NODELABEL(adc));
	if(adc_dev == NULL){
		printf("issue..\n");
	}
	
	err = adc_channel_setup(adc_dev, &m_1st_channel_cfg);
    if (err) {
	    printk("Error in adc setup: %d\n", err);
	}
	
	const struct adc_sequence sequence = {
		.channels = BIT(ADC_1ST_CHANNEL_ID),
		.buffer = m_sample_buffer,
		.buffer_size = sizeof(m_sample_buffer),
		.resolution = ADC_RESOLUTION,
		.oversampling = 4,
	};

	err = adc_read(adc_dev, &sequence);
	if (err) {
        printk("adc_read() failed with code %d\n", err);
	}
	float val;
	for (int i = 0; i < BUFFER_SIZE; i++) {
                //printk("ADC raw value: %d\n", m_sample_buffer[i]);
				//3.6 is max voltage with internal_ref (0.6V) and 1/6 Gain.
                val = (float)m_sample_buffer[i]*3.6/pow(2,ADC_RESOLUTION);
	}
	printk("val %f \r\n",val);
	k_work_init(&work_bas, update_coincell_level);
	k_timer_init(&timer_bas, time_to_update_battery_service,NULL);
	k_timer_start(&timer_bas,K_SECONDS(1),K_SECONDS(10));
        
}


float getVoltage(){

	int ret;
	const struct adc_sequence sequence = {
		.channels = BIT(ADC_1ST_CHANNEL_ID),
		.buffer = m_sample_buffer,
		.buffer_size = sizeof(m_sample_buffer),
		.resolution = ADC_RESOLUTION,
		.oversampling = 4,
	};

	if (!adc_dev) {
		return -1;
	}

	ret = adc_read(adc_dev, &sequence);
	if (ret) {
        printk("adc_read() failed with code %d\n", ret);
	}
	float val;
	for (int i = 0; i < BUFFER_SIZE; i++) {
                //printk("ADC raw value: %d\n", m_sample_buffer[i]);
				//3.6 is max voltage with internal_ref (0.6V) and 1/6 Gain.
                val = (float)m_sample_buffer[i]*3.6/pow(2,ADC_RESOLUTION);
	}
	battery_level(val);
	printk("val %f \r\n",val);
	return val;
}



void update_coincell_level(struct k_work *work){
	set_coincell_level(battery_level(getVoltage()));
};

void time_to_update_battery_service(){
	printk("timetosubmit\r\n");
	k_work_submit(&work_bas);
}

uint8_t battery_level(float adc_voltage){
	int bat = (uint8_t)(adc_voltage*100/3.1);
	printf("adc: %f bat: %i\n",adc_voltage, bat);
	return bat;
};

