"""
Manages USB device monitoring for Arduino controllers and XIMEA cameras.

@author: Georg Schwendt
date: 2025-09
"""

from PySide6.QtCore import QThread, Signal
from usbmonitor import USBMonitor
from usbmonitor.attributes import ID_MODEL, ID_MODEL_ID, ID_VENDOR_ID

from serial.tools import list_ports

class USBWatcher(QThread):
    usb_event = Signal(str, str, dict)  # action ("add"/"remove"), formatted device string
    
    ARDUINO_VID = "2341"
    DFROBOT_VID = "3343"
    XIMEA_VID = "20F7"
    #XIMEA_VID2 = "20f7" 

    device_filter_tuple= ( # devices with other VIDs will NOT trigger a USB added/disconnected event
        {"ID_VENDOR_ID": ARDUINO_VID},
        {"ID_VENDOR_ID": DFROBOT_VID},
        {"ID_VENDOR_ID" : XIMEA_VID},
        {"ID_VENDOR_ID" : XIMEA_VID.lower()} #usbmonitor library does internal string comparison when filtering devices (filter_devices argument in constructor).  depending on os VID is upper or lower case 
    )


    def __init__(self):
        super().__init__()
        self._monitor = USBMonitor(filter_devices=self.device_filter_tuple) # consider removing argument if debugging with new devices
        self.controllers = {}
        self.cameras = {}

        self.get_controllers()
        self.get_cameras()

        self.number_of_controllers = len(self.controllers)
        self.number_of_cameras = len(self.cameras) # number of ximea cameras can also be inferred through xiapi.Camera().get_number_devices() function


    # Formatter & filter for Arduino (or clone)/Ximea devices
    @staticmethod
    def device_filter(info):
        vid = info.get(ID_VENDOR_ID).upper()
        if vid == USBWatcher.ARDUINO_VID or vid == USBWatcher.DFROBOT_VID:
            #print(f"device_filter: Detected controller: {info}")
            return "controller"
        elif vid == USBWatcher.XIMEA_VID:
            #print(f"device_filter: Detected camera: {info}")
            return "camera"
        else:
            return None  # skip non-Arduino (or clone)/XIMEA devices
        #model = info.get(ID_MODEL, "Unknown")
        #pid = info.get(ID_MODEL_ID, "??")
        #return f"{model} (VID:PID={vid}:{pid})"

    # Thread entry point
    def run(self):
        def on_connect(dev_id, info):
            #print(f"on_connect: dev_id: {dev_id}, info: {info}")
            type_of_device = self.device_filter(info)
            if not type_of_device:
                return
            #print("number of controllers: on_connect_start", self.number_of_controllers)
            if ("controller" in type_of_device) and (self._add_serial_port_to_USB_info(dev_id, info) is not None):
                self.number_of_controllers += 1
                #print("number of controllers: on_connect_end", self.number_of_controllers)
                #self.usb_event.emit("added", type_of_device, info)
            elif "camera" in type_of_device:
                self.cameras[dev_id] = info
                self.number_of_cameras += 1
            self.usb_event.emit("added", type_of_device, info)

        def on_disconnect(dev_id, info):
            #print("on_disconnect: USB disconnect registered")
            type_of_device = self.device_filter(info)
            if not type_of_device: 
                return
            #print("on_disconnect: type of device", type_of_device)
            if "controller" in type_of_device:
                #self.usb_event.emit("removed", type_of_device)
                self.controllers.pop(dev_id, None)
                self.number_of_controllers -= 1
            elif "camera" in type_of_device:
                self.cameras.pop(dev_id, None)
                self.number_of_cameras -= 1
            self.usb_event.emit("removed", type_of_device, info)

        self._monitor.start_monitoring(
            on_connect=on_connect,
            on_disconnect=on_disconnect
        )

    def get_controllers(self):
        self.controllers = {}
        connected_devices = self._monitor.get_available_devices()
        if not connected_devices:
            return None

        for dev_id, info in connected_devices.items():
            type_of_device = self.device_filter(info)
            if "controller" in type_of_device:
                self._add_serial_port_to_USB_info(dev_id, info)
        return self.controllers
    
    def get_cameras(self):
        self.cameras = {}
        connected_devices = self._monitor.get_available_devices()

        if not connected_devices:
            return None
        
        for device, info in connected_devices.items():
            type_of_device = self.device_filter(info)
            if type_of_device == "camera":
                self.cameras[device] = info
        return self.cameras 

    def _add_serial_port_to_USB_info(self, id, info):
        ports = list_ports.comports()
        for port in ports:
            #print(f"_add_serial_port_to_USB_info: device: {port.device}, description: {port.description}, hwid: {port.hwid}, serial number: {port.serial_number}")
            if not port.serial_number:
                continue
            if port.serial_number in info["ID_SERIAL"]:
                info["serial_port"] = port.device
                self.controllers[id] = info
                return 0
        return None

    # Stop the monitor gracefully
    def stop(self):
        self._monitor.stop_monitoring()
        self.quit()
        self.wait()
