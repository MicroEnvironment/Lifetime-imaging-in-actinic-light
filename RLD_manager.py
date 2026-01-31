"""
Manages camera settings, serial communication with the RLD controller,
image acquisition, and post-processing for lifetime calculation.

@author: Georg Schwendt
date: 2025-09
"""

from ximea import xiapi
import numpy as np
from dataclasses import dataclass
import cv2
import time
import configparser
import os

@dataclass
class ImagingParameters:
    # acquisition parameters
    sets_to_acquire: int = 3
    exposure_time_us: int = 20
    delay_window1_us: float = 2.5   
    delay_window2_us: float = 22.5
    pulse_width_us: float = 40.0
    exposures_per_frame: int = 10
    end_delay_us: int = 33  # necessary. was determined experimentally
    light_intensity: int = 100  # corrected variable name
        


class RLD:
    def __init__(self):
        # both camera and serial connection are managed outside of this class and passed to it when needed
        self.camera = None
        self.serial_connection = None
        self.img = xiapi.Image()
        self.image_dict = {'window1': [], 'window2': [], 'dark' : []} #contains acquired images
        self.average_lifetime = None 
        self.params = ImagingParameters()

        # metadata of acquired images
        self.start_time_ns = None 
        self.end_time_ns = None
        self.start_time_localtime = None
        self.start_time_localtime_ms = None #milliseconds part of the start time, for more precise timing (conversion to localtime loses ms)
        self.end_time_localtime = None
        self.end_time_localtime_ms = None #milliseconds part of the end time, for more precise timing (conversion to localtime loses ms)
        self.image_start_time_dict = {'window1': [], 'window2': [], 'dark' : []} #timestamps in ns 

    #@staticmethod
    #def read_until(ser, terminator, timeout=2):
    #    response = ""
    #    while True:
    #        if ser.in_waiting > 0:
    #            data = ser.read(ser.in_waiting).decode('utf-8')
    #            response += data
    #            if response.__contains__(terminator):
    #                break
    #        if time.time() - start_time > timeout:
    #            print("Timeout. No complete response from Arduino.")
    #            break
    #    return response


#region active imaging

    def attach_hardware(self, camera=None, serial=None):
        if camera: self.camera = camera
        if serial: self.serial_connection = serial

    def init_camera(self):
        self.camera.set_imgdataformat("XI_RAW16")  # 16 bit images. Needs to be debayered later if RGB camera is used. RGB48 not yet implemented in ximea API

        self.camera.set_exposure(self.params.exposure_time_us)

        #sensor defect correction
        self.camera.enable_bpc()

        self.camera.disable_aeag() #disable auto exposure
        self.camera.set_gammaY(1.0) #disable luminosity gamma correction
        self.camera.set_gammaC(1.0) #disable chromatic gamma correction

        #Non-isolated GPIO port 2 as self.cameraera trigger.
        #will need to be changed to isolated GPI 1 if frame-active signal is to be used as well, or additional
        #cicuitry will be needed to make use of the isolated GPO 1 as either frame active or exposure active signal
        #or exposure active can be dropped.
        self.camera.set_gpi_selector("XI_GPI_PORT2")
        self.camera.set_gpi_mode("XI_GPI_TRIGGER")
        self.camera.set_trigger_source('XI_TRG_EDGE_RISING')
        self.camera.set_trigger_selector("XI_TRG_SEL_EXPOSURE_START")
        self.camera.set_exposure_burst_count(self.params.exposures_per_frame) 

        #Non-isolated GPIO port 3 as "exposure active" output -> observe with oscilloscope
        self.camera.set_gpo_selector("XI_GPO_PORT3")
        self.camera.set_gpo_mode("XI_GPO_EXPOSURE_ACTIVE")

        #in case camera specific settings (different timings mainly end_delay(!)) are required
        #device_name = cam.get_device_name(buffer_size=256)
        #sensor_model_id = cam.get_sensor_model_id()

        #check if camera is monochrome or rgb and apply settings accordingly
        if self.camera.is_iscolor():
            #settings specific to RGB cameras
            self.camera.disable_auto_wb() #disable white balance
        else:
            #settings specific to monochrome cameras
            self.camera.set_binning_vertical_mode("XI_BIN_MODE_SUM") # average not supported (yet?)
            self.camera.set_binning_horizontal_mode("XI_BIN_MODE_SUM")

            # binning increases sensitivity and readout time but decreases resolution
            # 2x2 binning is the maximum for the camera and a good compromise
            self.camera.set_binning_vertical(2)
            self.camera.set_binning_horizontal(2)


    def init_rld_controller(self):
        # currently interframe delay is hardcoded in the Arduino firmware to 25 ms
        settings_str = f"R,{self.params.exposure_time_us},{self.params.delay_window1_us - 0.75 + self.params.pulse_width_us},{self.params.delay_window2_us - 0.75 + self.params.pulse_width_us},{self.params.exposures_per_frame},\
        {self.params.light_intensity},{self.params.pulse_width_us},{self.params.end_delay_us},{self.params.sets_to_acquire}\n" 
        self.serial_connection.write(settings_str.encode())

        #print(self.read_until(serial_connection, "END\n"))


    def acquire_images(self):
        self.camera.start_acquisition()
        
        self.serial_connection.write(b"A\n") #send "acquisition" command to RLD controller

        self.image_dict = {'window1': [], 'window2': [], 'dark' : []} #clear previous images
        current_set = 0
        self.start_time_ns = time.time_ns()
        while current_set < self.params.sets_to_acquire:
            for key in self.image_dict:
                self.camera.get_image(self.img) 
                self.image_start_time_dict[key].append(self.camera.get_timestamp()) #timestamp in ns. Not bound to system time, but to camera internal clock -> relative times are accurate
                #for xiC cameras it is recorded at the start of the exposure
                data_raw = self.img.get_image_data_numpy()
                self.image_dict[key].append(data_raw)

            current_set += 1    
        self.end_time_ns = time.time_ns()
        print(self.image_start_time_dict)
        #print timestamps in yyyy-mm-dd hh:mm:ss.ssssss format
        self.camera.stop_acquisition()

        self.start_time_localtime = time.localtime(self.start_time_ns / 1e9)
        self.start_time_localtime_ms = (self.start_time_ns % 1e9) / 1e6
        self.end_time_localtime = time.localtime(self.end_time_ns / 1e9)
        self.end_time_localtime_ms = (self.end_time_ns % 1e9) / 1e6
        print(f"Acquisition started at: {time.strftime('%Y-%m-%d %H:%M:%S', self.start_time_localtime)}:{self.start_time_localtime_ms:.3f}")
        print(f"Acquisition ended at: {time.strftime('%Y-%m-%d %H:%M:%S', self.end_time_localtime)}:{self.end_time_localtime_ms:.3f}")

    def run(self):
        if self.serial_connection is None or self.camera is None:
            print("Camera or serial connection not attached.")
            return -1 #RLD not properly initialized
        self.init_camera()
        self.init_rld_controller()
        self.acquire_images()
        if self.camera.is_iscolor():
            # debayer images in case of RGB camera
            # must be done before calculating average lifetime since debayering required uint8 or uint16 input
            for key in self.image_dict:
                self.image_dict[key] = [cv2.cvtColor(img, cv2.COLOR_BAYER_BG2RGB) for img in self.image_dict[key]]
        self.calculate_average_lifetime()

#endregion

#region load old measurements

    def load_settings_from_file(self, config_path="settings.conf"):
        settings = configparser.ConfigParser()
        if os.path.exists(config_path):
            settings.read(config_path)
            params = ImagingParameters()
            if 'ImagingParameters' in settings:
                try:
                    params.exposure_time_us = settings.getint('ImagingParameters', 'exposure_time_us', fallback=-1)
                    params.delay_window1_us = settings.getfloat('ImagingParameters', 'delay_window1_us', fallback=-1) 
                    params.delay_window2_us = settings.getfloat('ImagingParameters', 'delay_window2_us', fallback=-1)
                    params.end_delay_us = settings.getint('ImagingParameters', 'end_delay_us', fallback=-1)
                    params.pulse_width_us = settings.getfloat('ImagingParameters', 'pulse_width_us', fallback=-1)
                    params.light_intensity = settings.getint('ImagingParameters', 'light_intensity', fallback=-1)
                    params.sets_to_acquire = settings.getint('ImagingParameters', 'sets_to_acquire', fallback=-1)
                    params.exposures_per_frame = settings.getint('ImagingParameters', 'exposures_per_frame', fallback=-1)
                except ValueError as e:
                    print(f"Error reading config file: {e}")
                    return -2 # corrupt config file
                
                #print("parameters from config file:", params)
                if any(value == -1 for value in vars(params).values()): 
                    print(f"Some parameters are missing or invalid in the config file. {params}")
                    return -1 # missing parameters in config file
                       
                self.params = params  # Update self.params if valid
                return 0 # success

            elif "Settings" in settings: #legacy support
                try:
                    params.exposure_time_us = settings.getint('Settings', 'exposure_us', fallback=-1)
                    #params.delay_window1_us = settings.getint('Settings', 'delay_window1_us', fallback=-1) #disable for now, since it is not implemented yet
                    params.delay_window2_us = settings.getint('Settings', 'delay_window2_us', fallback=-1)
                    params.end_delay_us = settings.getint('Settings', 'end_delay_us', fallback=-1)
                    params.pulse_width_us = settings.getint("Settings", "light_pulse_width_us", fallback=-1)
                    params.light_intensity = settings.getint('Settings', 'light_intensity', fallback=-1)
                    params.sets_to_acquire = settings.getint('Settings', 'sets_to_acquire', fallback=-1)
                    params.exposures_per_frame = settings.getint('Settings', 'exposures_per_frame', fallback=-1)
                except ValueError as e:
                    print(f"Error reading legacy config file: {e}")
                    return -2
                #print("parameters from legacy config file:", params)
                if any(value == -1 for value in vars(params).values()):
                    return -1
                self.params = params  # Update self.params if valid      
                return 0          
            
        return None

    def load_images_from_folder(self, data_folder_path):
        window1_images = []
        window2_images = [] 
        dark_images = []
        #at the moment, this does not load lifetime images, only raw images. lifetime images can be calculated after loading
        #config file is needed for lifetime calculation from the loaded raw images + parameters
        config_path = ""
        for file_name in os.listdir(data_folder_path):
            if file_name.__contains__("window1_") and file_name.endswith(".tif"):
                #print("loading window1:", file_name)
                window1_images.append(cv2.imread(os.path.join(data_folder_path, file_name), cv2.IMREAD_UNCHANGED))
            elif file_name.__contains__("window2_") and file_name.endswith(".tif"):
                #print("loading window2:", file_name)
                window2_images.append(cv2.imread(os.path.join(data_folder_path, file_name), cv2.IMREAD_UNCHANGED))
            elif (file_name.startswith("dark_") or file_name.__contains__("background_")) and file_name.endswith(".tif"): #background_ for legacy reasons
                #print("loading dark:", file_name)
                dark_images.append(cv2.imread(os.path.join(data_folder_path, file_name), cv2.IMREAD_UNCHANGED))
        #    elif file_name.endswith(".conf"):
        #        config_path = os.path.join(data_folder_path, file_name)

        if not window1_images or not window2_images or not dark_images:
            #print("Error: Missing image sets in the selected folder.")
            return None
        elif len(window1_images) != len(window2_images) or len(window1_images) != len(dark_images) or len(window2_images) != len(dark_images):
            #print("Error: Unequal number of images in the sets.")
            self.image_dict = {'window1': window1_images, 'window2': window2_images, 'dark': dark_images} 
            # lifetime can still be calculated from unequal sets, but user should be warned
            return -1
        else:
            self.image_dict = {'window1': window1_images, 'window2': window2_images, 'dark': dark_images}
            return 0
        #config_file_status = self.load_settings_from_file(config_path)

        #return config_file_status # None if no config file found, -1 if missing parameters, -2 if corrupt, 0 if success

#endregion


# region post-processing

    @staticmethod
    def arr_replace_negatives_by_nan(arr):
        arr_copy = np.copy(arr)*1.0
        arr_copy[arr_copy < 0] = np.nan
        return arr_copy

    @staticmethod
    def arr_replace_zeroes_by_nan(arr):
        arr_copy = np.copy(arr)*1.0
        arr_copy[arr_copy == 0] = np.nan
        return arr_copy

    @staticmethod
    def calculate_lifetime(delta_t, arr_window1, arr_window2):
        return delta_t / RLD.arr_replace_negatives_by_nan(RLD.arr_replace_zeroes_by_nan((np.log(RLD.arr_replace_zeroes_by_nan(arr_window1) / RLD.arr_replace_zeroes_by_nan(arr_window2)))))

    def calculate_average_lifetime(self):
        delta_t = self.params.delay_window2_us - self.params.delay_window1_us
        dark_avg = np.average(self.image_dict["dark"], axis=0)
        window1_avg = np.average(self.image_dict["window1"], axis=0) - dark_avg
        window2_avg = np.average(self.image_dict["window2"], axis=0) - dark_avg
        self.average_lifetime = RLD.calculate_lifetime(delta_t, window1_avg, window2_avg)

# endregion

