#include "event.h"

bool RESETTED = true;

extern void phyphox_event_received(){
    printk("PHYPHOX EVENTHANDLER\r\n");
    /*
    00: Pause
    01: Play
    02: Clear
    FF: Sync
    */

    if(event_data.config[0]==0x01 && RESETTED){
        global_timestamp = k_uptime_ticks()/32768.0;
        RESETTED=false;
        
        /* room for improvement*/
        bmp_data.current_event = 0;
        lsm_data.event_number = 0;
        event_data.RUNNING = true;
        printk("global reset\r\n");
    }
    if(event_data.config[0]==0x02){
        RESETTED=true;
        event_data.RUNNING = false;
        //bmp_data.current_event = 0;
        //lsm_data.event_number = 0;
    }
};