"""
Manages USB device monitoring for Arduino controllers and XIMEA cameras.

@author: Georg Schwendt
date: 2025-09
"""

from PySide6.QtCore import QThread, Signal
from usbmonitor import USBMonitor
from usbmonitor.attributes import ID_MODEL, ID_MODEL_ID, ID_VENDOR_ID


class USBWatcher(QThread):
    usb_event = Signal(str, str)  # action ("add"/"remove"), formatted device string
    
    ARDUINO_VID = "2341"
    XIMEA_VID = "20F7"

    device_filter_tuple= (
        {"ID_VENDOR_ID": ARDUINO_VID},
        {"ID_VENDOR_ID" : XIMEA_VID} 
    )


    def __init__(self):
        super().__init__()
        self._monitor = USBMonitor(filter_devices=self.device_filter_tuple)
        self.controllers = {}
        self.cameras = {}

        self.update_controllers()
        self.update_cameras()

        self.number_of_controllers = len(self.controllers)
        self.number_of_cameras = len(self.cameras) # number of ximea cameras can also be inferred through xiapi.Camera().get_number_devices() function


    # Formatter & filter for Arduino/Ximea devices
    @staticmethod
    def device_filter(info):
        vid = info.get(ID_VENDOR_ID)
        if vid == USBWatcher.ARDUINO_VID:
            return "controller"
        elif vid == USBWatcher.XIMEA_VID:
            #print(f"Detected camera: {info}")
            return "camera"
        else:
            return None  # skip non-Arduino/XIMEA devices
        #model = info.get(ID_MODEL, "Unknown")
        #pid = info.get(ID_MODEL_ID, "??")
        #return f"{model} (VID:PID={vid}:{pid})"

    # Thread entry point
    def run(self):
        def on_connect(dev_id, info):
            #print(f"dev_id: {dev_id}, info: {info}")
            type_of_device = self.device_filter(info)
            if "controller" in type_of_device:
                self.usb_event.emit("added", type_of_device)
                self.controllers[dev_id] = info
                self.number_of_controllers += 1
            elif "camera" in type_of_device:
                self.usb_event.emit("added", type_of_device)
                self.cameras[dev_id] = info
                self.number_of_cameras += 1

        def on_disconnect(dev_id, info):
            type_of_device = self.device_filter(info)
            if "controller" in type_of_device:
                self.usb_event.emit("removed", type_of_device)
                self.controllers.pop(dev_id, None)
                self.number_of_controllers -= 1
            elif "camera" in type_of_device:
                self.usb_event.emit("removed", type_of_device)
                self.cameras.pop(dev_id, None)
                self.number_of_cameras -= 1

        self._monitor.start_monitoring(
            on_connect=on_connect,
            on_disconnect=on_disconnect
        )

    def update_controllers(self):
        self.controllers = {}
        connected_devices = self._monitor.get_available_devices()
        if not connected_devices:
            return None

        for device, info in connected_devices.items():
            type_of_device = self.device_filter(info)
            if "controller" in type_of_device:
                self.controllers[device] = info
    
    def update_cameras(self):
        self.cameras = {}
        connected_devices = self._monitor.get_available_devices()

        if not connected_devices:
            return None
        
        for device, info in connected_devices.items():
            type_of_device = self.device_filter(info)
            if type_of_device == "camera":
                self.cameras[device] = info
        return self.cameras 



    # Stop the monitor gracefully
    def stop(self):
        self._monitor.stop_monitoring()
        self.quit()
        self.wait()
