A graphical user interface for time-gated luminescence lifetime imaging based on a [publication of Rousseau et al](https://doi.org/10.1016/j.snb.2025.138849) (see same publication for how the imaging method works). In addition to having a graphical user interface, the newly developed software has some advantages over the [original script](https://gitlab.com/groussea/clicc), most importantly more precise control of timings, also allowing overlapping trigger pulses for camera and light source, which means other timing schemes like [frame-straddling](https://doi.org/10.1021/acssensors.4c01828) for [SensPIV](https://doi.org/10.1016/j.crmeth.2022.100216) can also be used. 

Additionally, a simple script for running measurement schemes with synchronized actinic light (actinic_light_control.ipynb) using a KL2500 (Schott GmbH) light source is included. The actinic light source is controlled via the USB connection to the Computer (i.e. not the microcontroller), resulting in some jitter (tens of ms). A more general solution is in development.

# Requirements

Note: Most code should be OS cross-platform compatible (Windows, Linux, MacOS) , but this is not fully tested yet. Code for automatic connection to the microcontroller board will need to be changed.
### Hardware: 
- XIMEA xiC USB cameras supporting "multiple exposures in one frame" mode (currently implemented and tested: MC124MG-SY, MC050CG-SY)
- Arduino Uno rev 3
- Computer 
- Sufficiently fast excitation light source that accepts TTL trigger signals. Wavelength depends on used indicator. 0-5 V analog modulation input (optional, to control light intensity with )
- Schott KL2500 (only for actinic light)
- DAC module (optional, [DFRobot Gravity: I2C 12-Bit DAC Module](https://www.dfrobot.com/product-1721.html)used here)
- Cables (Camera sync cable, USB...)
### Software:
- This repository
- Python 3.10 or higher and packages in requirements.txt (creating a virtual environment recommended)
- [XIMEA python API](https://www.ximea.com/support/wiki/apis/python)
- Arduino IDE with digitalWriteFast and DFRobot_MCP4725 libraries

# User Guide

## Installation
- After installation of required software, flash rld_controller.ino onto the Arduino Uno Rev3 using the Arduino IDE.
- Confirm that the Ximea camera works with the Ximea CamTool
## Hardware connections
- Connect GND of Ximea camera and GND of excitation light source trigger input to any of the GND pins on the Arduino Uno Rev3
- Connect Pin 9 of the Arduino Uno Rev3 to non-isolated GPIO 1 of Ximea Camera
- Connect Pin 10 of the Arduino Uno Rev3 to the trigger input of the excitation light source
- (optional for control of excitation light intensity): connect DAC module to Arduino board according to manufacturers instructions and set address switch to 0x60. 
- (optional for actinic light control): connect the Schott KL2500 to the PC via USB
## GUI
### -Start
- Close all software that is connected to the camera or microcontroller board
- Run main.py to start the program
- Click "Image $\tau$" button to start the acquisition
### -Connection status
- ✅: Device connected and ready to use
- 🔌: Device found (connected to USB) but not connected to imaging software
- ❌: Device not found
### -Settings
- Exposure (µs): Exposure time of the camera
- **Delay 1** (µs): Delay between end of excitation pulse and start of exposure of first window. Takes the input delay of camera into consideration but NOT fall time of LED and propagation delay. Step size: 62.5 ns. Negative values can be set for trigger pulse which overlaps with excitation pulse -> frame-straddling
- **Delay 2** (µs): Delay between end of excitation pulse and start of exposure of second window. Takes the input delay of camera into consideration. Step size: 62.5 ns
- **End delay** (µs): delay after exposure 1 has ended. Needed for the camera to accept a new trigger signal in the next exposure. If too short -> timeout error
- **Pulse width** (µs): pulse width of excitation light trigger signal. Does not take any delays into account
- **Exposures/frame**: number of exposures per frame (1-4095). Use to tune signal intensity of window 1 and 2. 
- **Light intensity** (%): excitation light intensity in percent. Requires external DAC module
- **Sets to acquire**: number of sets to acquire in the measurement (a set consists of a complete pulsing sequence with window 1, 2 and dark frames). The lifetime of a measurement is averaged to increase the signal to noise ratio.

## Actinic light control script
- Open the jupyter notebook actinic_light_control.ipynb in your editor of choice
- adjust actinic light intensities and timing sequence to your requirements and run the code from top to bottom

# Citation

If you use this code in your publication, please cite: pending
