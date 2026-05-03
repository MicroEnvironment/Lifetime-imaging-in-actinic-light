/*
Arduino Uno Rev3 Firmware for time-gated lifetime imaging.
Reads serial command with timing settings from PC and sends defined 5V trigger pulses
to camera (pin 10) and excitation light source (pin 11).
Controls excitation light intensity via external DAC module (optional)

@author: Georg Schwendt
date: 2025-09
*/


#include <digitalWriteFast.h>
#include "DFRobot_MCP4725.h" // DAC module to control excitaction light intensity

String command;
inline void triggerLightPulse() __attribute__((always_inline));

bool background_subtraction = true; //not sure if there's a case where background correction won't be performed. Leaving in for now.
int exposures_per_frame = 4095; // max 4095. Use to tune signal intensity 
int sets_to_acquire;

float light_pulse_width_us; //float to allow sub-microsecond pulses   

int window_1_delay_cycles;  
int window_2_delay_cycles;
int light_pulse_width_cycles;
int exposure_pulse_width_cycles;
int period_cycles;


int offset_cycles = 17; //allows triggering camera exposure up to 250 ns before light trigger -> frame straddeling 
//1 us offset, 750 input delay on camera. 

int NIR_pin = 7; // for special setup that enables recording NIR videos between lifetime images. 

int current_frame_type = 0; // 0 = window1, 1 = window2, 2 = background

int end_2_delay_us; // = exposure_us + end_delay_us;
int end_1_delay_us; // = end_2_delay_us - window_1_delay_us + window_2_delay_us; //ensure consistent pulse timings

int camera_trigger_pin = 9; //mapped to timer1 compare match channel A
int light_trigger_pin = 10; //mapped to timer1 compare match channel B

long ref_voltage_mv = 5000; // reference voltage (= Vcc on Arduino board) in mV used for the DAC unit. Uncalibrated
//on the LPS3 LED engine, 0-5V corresponds to 0-100% light intensity
//long variable to avoid overflow in later calculation
int DAC_voltage_mv = 0;

DFRobot_MCP4725 DAC;



void setup() 
{
  pinMode(camera_trigger_pin, OUTPUT);
  pinMode(light_trigger_pin, OUTPUT);
  pinMode(NIR_pin, OUTPUT);
  digitalWriteFast(camera_trigger_pin, LOW); //triggers on HIGH
  digitalWriteFast(light_trigger_pin, LOW); //set led exposure to LOW (triggers on HIGH)
  digitalWriteFast(NIR_pin, LOW);


  //timer1 setup
  //normal mode
  TCCR1A = 0;
  TCCR1B = 0;

  TCCR1A |= (1 << COM1A0) | (1 << COM1B0); // toggle mode for both pin 9 (camera, channel A) and pin 10 (light, channel B) 
  
  //MCP4725A0_IIC_Address0 -->0x60
  //MCP4725A0_IIC_Address1 -->0x61
  DAC.init(MCP4725A0_IIC_Address0, ref_voltage_mv); //switch on DAC module must be set accordingly!
  DAC.outputVoltage(DAC_voltage_mv);
  command.reserve(32);
  Serial.begin(115200);
  while (!Serial) 
  {
    ; // Wait for serial port to connect.
  }
  Serial.println("Arduino ready to receive commands.");
}

void loop()  //poll for serial command from computer and execute it
{
  if (Serial.available() > 0) 
  {
    command = Serial.readStringUntil('\n');
    if (command.startsWith("R,")) //settings string
    {
      parseSettings(command);
    } 
    else if (command == "T")
    {
      //toggle pin 7 (NIR LEDs)
      digitalWrite(NIR_pin, !digitalRead(NIR_pin));
    }
    else if (command == "A") //start acquisition
    {
      int frames;
      if(background_subtraction)
      {
        frames = 3;
      }
      else
      {
        frames = 2;
      }
      //Serial.println("triggering\n");
      for (int current_set = 0; current_set < sets_to_acquire; current_set++)
      {

        for (int current_frame = 0; current_frame < frames; current_frame++)
        {
          startAcquisition(current_frame);
          delay(25); //manual delay to give camera time to accept next trigger
          //checking the "frame active" signal would require logic level shifter between microcontroller board and 
        }
      }

      if (background_subtraction) 
      {
        if (current_frame_type > 2)
        {
          current_frame_type = 0;
        }
      }
      else if (current_frame_type > 1)
      {
        current_frame_type = 0;
      }      
    } 
    // else if (command == "B") 
    // {
    //   for(int j =0; j < 1200; j++)
    //   {
    //     noInterrupts();
    //     triggerWindow(offset_cycles, 32000, 5, 31988, 32100);
    //     interrupts();
    //     delay(38);
    //   }
    // }
  }
}

void startAcquisition(int frame_type)   //runs pulse sequence for time-gated lifetime measurements
{
  noInterrupts();
  int exp_counter = 0;

  int light_pulse_end_cycles = offset_cycles + light_pulse_width_cycles;
  int exposure_end_cycles;
  switch(frame_type) //
  {
    case 0: //window 1 
      exposure_end_cycles = window_1_delay_cycles + exposure_pulse_width_cycles;
      while(exp_counter < exposures_per_frame)
      {
        //TCCR1A |= (1 << COM1A0) | (1 << COM1B0); // toggle mode for both pin 9 (camera, channel A) and pin 10 (light, channel B) 
        //OCR1A = window_1_delay_cycles;
        triggerWindow(offset_cycles, light_pulse_end_cycles, window_1_delay_cycles, exposure_end_cycles, period_cycles);
        exp_counter++;    
      } 
      break;
  case 1: //window 2
    exposure_end_cycles = window_2_delay_cycles + exposure_pulse_width_cycles;
    while(exp_counter < exposures_per_frame)
    {
      //TCCR1A |= (1 << COM1A0) | (1 << COM1B0); // toggle mode for both pin 9 (camera, channel A) and pin 10 (light, channel B) 
      //OCR1A = window_2_delay_cycles;
      triggerWindow(offset_cycles, light_pulse_end_cycles, window_2_delay_cycles, exposure_end_cycles, period_cycles);
      exp_counter++;
    }
    break;

  case 2: //dark image for background subtraction if enabled
    if(!background_subtraction)
    {
      break;
    }
    else
    {
      exposure_end_cycles = window_1_delay_cycles + exposure_pulse_width_cycles;
      TCCR1A &= ~(1 << COM1B0); // disconnect channel B from pin10 (light trigger)
      while(exp_counter < exposures_per_frame)
      {
        //no LED pulses, just exposures as above. exact timing is not so important here    
        //TCCR1A |= (1 << COM1A0) // toggle mode for pin 9 (camera)
        triggerWindow(offset_cycles, light_pulse_end_cycles, window_1_delay_cycles, exposure_end_cycles, period_cycles);
        exp_counter++;
      }
      TCCR1A |= (1 << COM1B0); //reconnect channel B to toggle pin10 (light trigger)
      break;
    }
  }
  interrupts(); //otherwise problems in serial communication can arise
}

//light pulse always triggers after offset, so no delay parameter required
void triggerWindow(int light_pulse_start_cycles, int light_pulse_end_cycles, int exposure_start_cycles, int exposure_end_cycles, int period_end_cycles)
{
  //initialization
  OCR1A = exposure_start_cycles;
  OCR1B = light_pulse_start_cycles;
  TIFR1 |= ((1 << OCF1A) | (1 << OCF1B));  //clear compare match flags
  TCNT1 = 0;   //reset timer counter
  TCCR1B |= (1 << CS10);   // no prescaler, start timer

  while (bit_is_clear(TIFR1, OCF1B)){} //wait until timer B compare match has occured -> excitation light toggled ON
  OCR1B = light_pulse_end_cycles; //set compare match value of channel B to switch light pulse off when light pulse is reached
  TIFR1 = _BV(OCF1B); //clear previous compare match flag for light trigger

  while (bit_is_clear(TIFR1, OCF1A)){} //wait until timer A compare match has occured -> exposure toggled ON
  OCR1A = exposure_end_cycles; //set compare match to switch camera trigger OFF again
  TIFR1 = _BV(OCF1A); //clear compare match flag for camera trigger

  //wait until exposure pulse is finished -> camera trigger pin toggled OFF  
  while (bit_is_clear(TIFR1, OCF1A)){} 
  TCCR1A &= ~(1 << COM1A0); // disconnect channel A from pin9 (camera trigger)
  OCR1A = period_end_cycles; // set timer to period end
  TIFR1 = _BV(OCF1A); // clear flag

  //wait until period is finished.
  while (bit_is_clear(TIFR1, OCF1A)){} //less jitter than polling for TCNT1 (i.e. while(TCNT1 < period_cycles){})
  TCCR1B = 0; // stop timer
  TCCR1A |= (1 << COM1A0); // reactivate toggle mode for pin9
}


void parseSettings(String settings) 
{
  //parses settings passed from the Python script to the microcontroller and implements them.
  float window_1_delay_us;
  float window_2_delay_us; 
  settings.remove(0, 2); // Remove the "R," prefix

  int commaIndex1 = settings.indexOf(',');
  int commaIndex2 = settings.indexOf(',', commaIndex1 + 1);
  int commaIndex3 = settings.indexOf(',', commaIndex2 + 1);
  int commaIndex4 = settings.indexOf(',', commaIndex3 + 1);
  int commaIndex5 = settings.indexOf(',', commaIndex4 + 1);
  int commaIndex6 = settings.indexOf(',', commaIndex5 + 1);
  int commaIndex7 = settings.indexOf(',', commaIndex6 + 1);

  int exposure_us = settings.substring(0, commaIndex1).toInt();
  window_1_delay_us = settings.substring(commaIndex1 + 1, commaIndex2).toFloat();
  window_2_delay_us = settings.substring(commaIndex2 + 1, commaIndex3).toFloat();
  exposures_per_frame = settings.substring(commaIndex3 + 1, commaIndex4).toInt();
  int light_intensity = settings.substring(commaIndex4 + 1, commaIndex5).toInt(); //Arduino Uno R3 does not have a DAC unit. External DAC (e.g. MCP4725) could be used here
  light_pulse_width_us = settings.substring(commaIndex5 + 1, commaIndex6).toFloat(); //
  int end_delay_us = settings.substring(commaIndex6 + 1, commaIndex7).toInt();
  sets_to_acquire = settings.substring(commaIndex7 +1).toInt();

  DAC_voltage_mv = light_intensity * ref_voltage_mv / 100;
  DAC.outputVoltage(DAC_voltage_mv);

  window_1_delay_cycles = offset_cycles + 16*window_1_delay_us;
  window_2_delay_cycles = offset_cycles + 16*window_2_delay_us;

  light_pulse_width_cycles = 16*light_pulse_width_us;
  exposure_pulse_width_cycles = 16*exposure_us;

  //python program needs to make sure that this is longer than both offset + light_pulse_width_cycles 
  //and the minimum trigger period of the camera (dependent both on settings and hardware)
  period_cycles = window_2_delay_cycles + exposure_pulse_width_cycles + 16*end_delay_us - 5.375*16; //subtracting 5.375 us for overhead 

  //Serial.print("Exposure in us: ");
  //Serial.println(exposure_us);
  //Serial.print("Window1 delay in us: ");
  //Serial.println(window_1_delay_us);
  //Serial.print("Window2 delay in us: ");
  //Serial.println(window_2_delay_us);
  //Serial.print("Exposures per Frame: ");
  //Serial.println(exposures_per_frame);
  //Serial.print("Light Intensity in %: ");
  //Serial.println(light_intensity); 
  //Serial.print("End Delay in us: ");
  //Serial.println(end_delay_us);
  //Serial.println("Sets to acquire: ");
  //Serial.println(sets_to_acquire);
  //Serial.println("DAC voltage in mV: ");
  //Serial.println(DAC_voltage_mv);
  //Serial.println("END\n");
}

