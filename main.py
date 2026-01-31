"""
Graphical user interface for time-gated lifetime imaging.

@author: Georg Schwendt
date: 2025-09
"""

import os
import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QLabel, QFileDialog, QMessageBox
from PySide6.QtUiTools import QUiLoader
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import QFile, QSize, Qt #QTimer
from usb_watcher import USBWatcher
import re
import serial
import RLD_manager
from RLD_manager import ImagingParameters  # Import the ImagingParameters class
import matplotlib.pyplot as plt
import numpy as np
import cv2
import configparser
import time
from ximea import xiapi

# Change cwd to file directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))


class ImageLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.image_array = None  # Store original image data
        self.vmin = None
        self.vmax = None
        self.cmap = 'gray'
        self._last_pixmap = None
        self.setAlignment(Qt.AlignCenter)

    def sizeHint(self):
        return QSize(257, 188) 

    def set_image(self, array, vmin=None, vmax=None, cmap='gray'):
        self.image_array = array
        self.vmin = vmin if vmin is not None else np.min(array)
        self.vmax = vmax if vmax is not None else np.max(array)
        self.cmap = cmap
        # Normalize and apply colormap
        array = array.astype(np.float32)  # Prevent overflow
        norm = np.clip((array - self.vmin) / (self.vmax - self.vmin), 0, 1)
        if cmap == 'gray':
            img_8bit = (norm * 255).astype(np.uint8)
            qimg = QImage(img_8bit.data, img_8bit.shape[1], img_8bit.shape[0], img_8bit.strides[0], QImage.Format_Grayscale8)
        else:
            cmapped = plt.get_cmap(cmap)(norm)
            img_8bit = (cmapped[:, :, :3] * 255).astype(np.uint8)
            qimg = QImage(img_8bit.data, img_8bit.shape[1], img_8bit.shape[0], img_8bit.strides[0], QImage.Format_RGB888)
        # Scale image to label size
        pixmap = QPixmap.fromImage(qimg)
        scaled_pixmap = pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled_pixmap)
        self.setScaledContents(False)
        self._last_pixmap = scaled_pixmap
        self.setAlignment(Qt.AlignCenter)

    def mouseMoveEvent(self, event):
        if self.image_array is not None and self._last_pixmap is not None:
            # Qt6: position() returns QPointF
            pt = event.position().toPoint()
            x, y = pt.x(), pt.y()
            lbl_w = self.width()
            lbl_h = self.height()
            pix_w = self._last_pixmap.width()
            pix_h = self._last_pixmap.height()
            x_offset = (lbl_w - pix_w) // 2
            y_offset = (lbl_h - pix_h) // 2
            if x_offset <= x < x_offset + pix_w and y_offset <= y < y_offset + pix_h:
                img_x = (x - x_offset) * self.image_array.shape[1] // pix_w
                img_y = (y - y_offset) * self.image_array.shape[0] // pix_h
                img_x = max(0, min(self.image_array.shape[1] - 1, img_x))
                img_y = max(0, min(self.image_array.shape[0] - 1, img_y))
                value = self.image_array[img_y, img_x]
                self.setToolTip(f"({img_x}, {img_y}): {value}")
            else:
                self.setToolTip("")
        else:
            self.setToolTip("")
        super().mouseMoveEvent(event)

    def resizeEvent(self, event):
        # Redraw image on resize and keep centered
        if self.image_array is not None:
            self.set_image(self.image_array, vmin=self.vmin, vmax=self.vmax, cmap=self.cmap)
        self.setAlignment(Qt.AlignCenter)
        super().resizeEvent(event)

def debug_sizes(ui):
    print(f"Main window size: {ui.size()}")
    print(f"Central widget size: {ui.centralwidget.size()}")
    print(f"Preview group size: {ui.preview_gb.size()}")
    print(f"Window1 label size: {ui.window1_lbl.size()}")
    print(f"Window1 size policy: {ui.window1_lbl.sizePolicy().horizontalPolicy()}")

def debug_image_label(label, name):
    print(f"\n=== {name} Debug ===")
    print(f"Size: {label.size()}")
    print(f"SizeHint: {label.sizeHint()}")
    print(f"MinimumSizeHint: {label.minimumSizeHint()}")
    print(f"MinimumSize: {label.minimumSize()}")
    print(f"MaximumSize: {label.maximumSize()}")
    print(f"BaseSize: {label.baseSize()}")
    print(f"SizePolicy: {label.sizePolicy().horizontalPolicy()}, {label.sizePolicy().verticalPolicy()}")
    print(f"Parent: {label.parent()}")
    print(f"Parent size: {label.parent().size() if label.parent() else 'None'}")


class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()

        # Load the .ui file using QUiLoader
        loader = QUiLoader()
        loader.registerCustomWidget(ImageLabel)
        ui_file = QFile("rld-gui.ui")  
        ui_file.open(QFile.ReadOnly)
        self.ui = loader.load(ui_file, None)
        ui_file.close()
        
        # disable measurement button until camera and controller are connected
        self.ui.image_tau_btn.setEnabled(False)

        self.usb_watcher = USBWatcher()
        self.usb_watcher.usb_event.connect(self.on_usb_event)
        self.usb_watcher.start()
        self.rld = RLD_manager.RLD()  # Initialize RLD manager.
        self.rld_list = []  #for recalling previous measurements in the session if needed. We only add to this list if we measure a new image set or load a previous one.


        config_file_status = self.rld.load_settings_from_file()  # Load settings from config file
        if config_file_status == 0:
            self.apply_settings_to_gui()  # Apply settings to GUI

        # the camera and serial connection are opened/closed in the main window class
        # other classes only get a reference to the camera/serial connection to use them
        self.camera = xiapi.Camera()
        if self.camera.get_number_devices() > 0:
            # no multi-camera support for now - open the first camera found
            self.camera.open_device()  # blocking and therefore significantly slows down startup (~4 sec) if a camera is connected.
        self.serial_connection = None  # Placeholder for serial connection
        if self.usb_watcher.number_of_controllers > 0:
            self.connect_serial()
            self.rld.serial_connection = self.serial_connection  # Pass the serial connection to RLD manager

        #print(dir(self.ui))  # This will list all the attributes (widgets) in the loaded UI

        self.update_camera_status_lbl()
        self.extract_gui_inputs()  # Extract initial GUI inputs
        self.connect_buttons()
        self.connect_combo_boxes()
        self.connect_sliders_and_spinboxes()
        self.plot_preview_images()
        self.connect_preview_signals()
        if self.camera.CAM_OPEN and self.serial_connection and self.serial_connection.is_open:
            self.ui.image_tau_btn.setEnabled(True)
        else:
            self.ui.image_tau_btn.setEnabled(False)

    def apply_settings_to_gui(self):
        self.ui.exposure_sb.setValue(self.rld.params.exposure_time_us)
        self.ui.delay1_sb.setValue(self.rld.params.delay_window1_us) 
        self.ui.delay2_sb.setValue(self.rld.params.delay_window2_us)
        self.ui.end_delay_sb.setValue(self.rld.params.end_delay_us)
        self.ui.pulse_width_sb.setValue(self.rld.params.pulse_width_us)
        self.ui.light_intensity_sb.setValue(self.rld.params.light_intensity)
        self.ui.sets_to_acquire_sb.setValue(self.rld.params.sets_to_acquire)
        self.ui.exposures_per_frame_sb.setValue(self.rld.params.exposures_per_frame)


# region device management

    def update_controller_status_lbl(self):
        if self.serial_connection and self.serial_connection.is_open:
            self.ui.controller_status_lbl.setText("✅") 
        elif self.usb_watcher.number_of_controllers > 0:
            self.ui.controller_status_lbl.setText("🔌")
        else:
            self.ui.controller_status_lbl.setText("❌")

    def update_camera_status_lbl(self):
        if self.camera.CAM_OPEN: 
            self.ui.camera_status_lbl.setText("✅")
            #print("set label to check")
        elif self.usb_watcher.number_of_cameras > 0:
            self.ui.camera_status_lbl.setText("🔌") # at the moment, this is never displayed because camera.open_device() blocks repaint of the widget (also freezes everything else)
            #could be fixed by threading the camera opening, but not a high priority at the moment
            #print("set label to plug")
        else:
            self.ui.camera_status_lbl.setText("❌")
            #print("set label to cross")

    def connect_serial(self):
        if self.usb_watcher.number_of_controllers > 0:
            #print("controller found")
            for dev_id, info in self.usb_watcher.controllers.items():
                #unfortunately, the controller is sometimes only recognized as a generic device, so we search for "COM" in the ID_MODEL field
                #we screen for the vendor ID in the USBWatcher class
                #to be updated to be more cross-platform in the future
                match = re.search(r'COM(\d+)', info.get("ID_MODEL", ""))
                if match:
                    com_port = f"COM{match.group(1)}"
                    #print(f"Found controller on {com_port}")
                    try:
                        self.serial_connection = serial.Serial(com_port, baudrate=115200, timeout=1)
                        #we should read the response here to confirm the correct device is connected 
                        self.update_controller_status_lbl()
                    except serial.SerialException as e:
                        print(f"Error opening serial port {com_port}: {e}")

    def on_usb_event(self, action, type_of_device):
        
        #a bit redundant, but ensures the status labels are always correct
        self.update_camera_status_lbl()
        self.update_controller_status_lbl()
        
        if action == "added":
            #print(f"USB event: {action} - {type_of_device}")
            if "controller" in type_of_device:
                if self.serial_connection is None:
                    self.connect_serial()

            if "camera" in type_of_device:
                if not self.camera.CAM_OPEN and self.camera.get_number_devices() > 0:
                    self.camera.open_device()
        if action == "removed":
            #print(f"USB event: {action} - {type_of_device}")
            if "controller" in type_of_device:
                #not a super clean solution, but it works for now
                #problem: user could connect multiple controllers
                if self.serial_connection and self.serial_connection.is_open:
                    self.serial_connection.close()
                self.serial_connection = None            
            if "camera" in type_of_device:
                if self.camera.CAM_OPEN:
                    # Close camera safely. camera.is_isexist() throws an error if the camera is already removed but CAM_OPEN is still True 
                    # is_isexist() seems completely useless
                    self.camera.close_device()
        self.update_controller_status_lbl()
        self.update_camera_status_lbl()

        # Enable or disable measurement button based on both devices being connected
        if self.camera.CAM_OPEN and self.serial_connection and self.serial_connection.is_open:
            self.ui.image_tau_btn.setEnabled(True)
        else:
            self.ui.image_tau_btn.setEnabled(False)


#endregion

#region GUI connections
    def connect_combo_boxes(self):
        self.ui.select_measurement_cb.currentIndexChanged.connect(self.change_measurement_selection)
    
    def connect_buttons(self):
        """Connect button signals to their respective slots."""
        self.ui.load_image_sets_btn.clicked.connect(self.load_measurement)
        self.ui.save_lifetime_image_btn.clicked.connect(self.save_lifetime_image)
        self.ui.image_tau_btn.clicked.connect(self.image_tau)
        self.ui.save_images_btn.clicked.connect(self.save_measurement_folder_dialog)
        self.ui.load_settings_btn.clicked.connect(self.load_settings)
        self.ui.save_settings_btn.clicked.connect(self.save_settings)
        self.ui.clear_measurements_btn.clicked.connect(self.clear_measurements)
        self.ui.clear_selected_measurement_btn.clicked.connect(self.clear_selected_measurement)  

    def connect_sliders_and_spinboxes(self):
        """Synchronize sliders and spinboxes."""
        # Window 1
        self.ui.window1_min_hs.valueChanged.connect(self.ui.window1_min_sb.setValue)
        self.ui.window1_min_sb.valueChanged.connect(self.ui.window1_min_hs.setValue)
        self.ui.window1_max_hs.valueChanged.connect(self.ui.window1_max_sb.setValue)
        self.ui.window1_max_sb.valueChanged.connect(self.ui.window1_max_hs.setValue)

        # Window 2
        self.ui.window2_min_hs.valueChanged.connect(self.ui.window2_min_sb.setValue)
        self.ui.window2_min_sb.valueChanged.connect(self.ui.window2_min_hs.setValue)
        self.ui.window2_max_hs.valueChanged.connect(self.ui.window2_max_sb.setValue)
        self.ui.window2_max_sb.valueChanged.connect(self.ui.window2_max_hs.setValue)

        # Dark Frame
        self.ui.dark_min_hs.valueChanged.connect(self.ui.dark_min_sb.setValue)
        self.ui.dark_min_sb.valueChanged.connect(self.ui.dark_min_hs.setValue)
        self.ui.dark_max_hs.valueChanged.connect(self.ui.dark_max_sb.setValue)
        self.ui.dark_max_sb.valueChanged.connect(self.ui.dark_max_hs.setValue)

        # Decay Time
        self.ui.lifetime_min_hs.valueChanged.connect(self.ui.lifetime_min_sb.setValue)
        self.ui.lifetime_min_sb.valueChanged.connect(self.ui.lifetime_min_hs.setValue)
        self.ui.lifetime_max_hs.valueChanged.connect(self.ui.lifetime_max_sb.setValue)
        self.ui.lifetime_max_sb.valueChanged.connect(self.ui.lifetime_max_hs.setValue)

        #delay spinboxes
        self.ui.delay1_sb.editingFinished.connect(self.validate_delay1_sb)
        self.ui.delay2_sb.editingFinished.connect(self.validate_delay2_sb)
        self.ui.pulse_width_sb.editingFinished.connect(self.validate_pulse_width_sb)

    def connect_preview_signals(self):
        # Window 1
        self.ui.window1_min_sb.valueChanged.connect(self.plot_window1_preview)
        self.ui.window1_max_sb.valueChanged.connect(self.plot_window1_preview)
        self.ui.window1_min_hs.valueChanged.connect(self.plot_window1_preview)
        self.ui.window1_max_hs.valueChanged.connect(self.plot_window1_preview)
        # Window 2
        self.ui.window2_min_sb.valueChanged.connect(self.plot_window2_preview)
        self.ui.window2_max_sb.valueChanged.connect(self.plot_window2_preview)
        self.ui.window2_min_hs.valueChanged.connect(self.plot_window2_preview)
        self.ui.window2_max_hs.valueChanged.connect(self.plot_window2_preview)
        # Dark Frame
        self.ui.dark_min_sb.valueChanged.connect(self.plot_dark_preview)
        self.ui.dark_max_sb.valueChanged.connect(self.plot_dark_preview)
        self.ui.dark_min_hs.valueChanged.connect(self.plot_dark_preview)
        self.ui.dark_max_hs.valueChanged.connect(self.plot_dark_preview)
        # Lifetime
        self.ui.lifetime_min_sb.valueChanged.connect(self.plot_lifetime_preview)
        self.ui.lifetime_max_sb.valueChanged.connect(self.plot_lifetime_preview)
        self.ui.lifetime_min_hs.valueChanged.connect(self.plot_lifetime_preview)
        self.ui.lifetime_max_hs.valueChanged.connect(self.plot_lifetime_preview)

#endregion

#region plotting

    def plot_window1_preview(self):
        if self.rld and self.rld.image_dict and len(self.rld.image_dict["window1"]) > 0:
            # for RGB images, preview only the first channel
            if self.rld.image_dict["window1"][0].ndim == 3 and self.rld.image_dict["window1"][0].shape[2] == 3:
                window1_img = self.rld.image_dict["window1"][0][:, :, 0]
            else:
                window1_img = self.rld.image_dict["window1"][0]
            window1_min = self.ui.window1_min_sb.value()
            window1_max = self.ui.window1_max_sb.value()
            self.ui.window1_lbl.set_image(window1_img, vmin=window1_min, vmax=window1_max, cmap='gray')
        else:
            # Clear the label if no window1 image is available
            self.ui.window1_lbl.clear()
            self.ui.window1_lbl.setText("No window 1 image")
            self.ui.window1_lbl.setAlignment(Qt.AlignCenter)

    def plot_window2_preview(self):
        if self.rld and self.rld.image_dict and len(self.rld.image_dict["window2"]) > 0:
            if self.rld.image_dict["window2"][0].ndim == 3 and self.rld.image_dict["window2"][0].shape[2] == 3:
                window2_img = self.rld.image_dict["window2"][0][:, :, 0]
            else:
                window2_img = self.rld.image_dict["window2"][0]
            window2_min = self.ui.window2_min_sb.value()
            window2_max = self.ui.window2_max_sb.value()
            self.ui.window2_lbl.set_image(window2_img, vmin=window2_min, vmax=window2_max, cmap='gray')
        else:
            # Clear the label if no window2 image is available
            self.ui.window2_lbl.clear()
            self.ui.window2_lbl.setText("No window 2 image")
            self.ui.window2_lbl.setAlignment(Qt.AlignCenter)

    def plot_dark_preview(self):
        if self.rld and self.rld.image_dict and len(self.rld.image_dict["dark"]) > 0:
            if self.rld.image_dict["dark"][0].ndim == 3 and self.rld.image_dict["dark"][0].shape[2] == 3:
                dark_img = self.rld.image_dict["dark"][0][:, :, 0]
            else:
                dark_img = self.rld.image_dict["dark"][0]
            dark_min = self.ui.dark_min_sb.value()
            dark_max = self.ui.dark_max_sb.value()
            self.ui.dark_lbl.set_image(dark_img, vmin=dark_min, vmax=dark_max, cmap='gray')
        else:
            # Clear the label if no dark image is available
            self.ui.dark_lbl.clear()
            self.ui.dark_lbl.setText("No dark image")
            self.ui.dark_lbl.setAlignment(Qt.AlignCenter)

    def plot_lifetime_preview(self):
        if self.rld and self.rld.average_lifetime is not None:
            if self.rld.average_lifetime.ndim == 3 and self.rld.average_lifetime.shape[2] == 3:
                lifetime_img = self.rld.average_lifetime[:, :, 0]
            else:
                lifetime_img = self.rld.average_lifetime
            lifetime_min = self.ui.lifetime_min_sb.value()
            lifetime_max = self.ui.lifetime_max_sb.value()
            self.ui.lifetime_lbl.set_image(lifetime_img, vmin=lifetime_min, vmax=lifetime_max, cmap='plasma')
        else:
            # Clear the label if no lifetime image is available
            self.ui.lifetime_lbl.clear()
            self.ui.lifetime_lbl.setText("No lifetime image")
            self.ui.lifetime_lbl.setAlignment(Qt.AlignCenter)

    def plot_preview_images(self):
        self.plot_window1_preview()
        self.plot_window2_preview()  
        self.plot_dark_preview()
        self.plot_lifetime_preview()

#endregion



#region Event handler functions

    def validate_delay1_sb(self):
        difference = self.ui.delay1_sb.value() % 0.0625
        if difference > 0:
            if (difference > 0.03125):
                new_value = self.ui.delay1_sb.value() + (0.0625 - difference)
            else:
                new_value = self.ui.delay1_sb.value() - difference                
            self.ui.delay1_sb.setValue(new_value)

    def validate_delay2_sb(self):
        difference = self.ui.delay2_sb.value() % 0.0625
        if difference > 0:
            if (difference > 0.03125):
                new_value = self.ui.delay2_sb.value() + (0.0625 - difference)
            else:
                new_value = self.ui.delay2_sb.value() - difference                
            self.ui.delay2_sb.setValue(new_value)

    def validate_pulse_width_sb(self):
        difference = self.ui.pulse_width_sb.value() % 0.0625
        if difference > 0:
            if (difference > 0.03125):
                new_value = self.ui.pulse_width_sb.value() + (0.0625 - difference)
            else:
                new_value = self.ui.pulse_width_sb.value() - difference                
            self.ui.pulse_width_sb.setValue(new_value)

    def image_tau(self):
        self.rld = RLD_manager.RLD() # prepare new measurement
        self.extract_gui_inputs()  # Ensure parameters are up to date
        self.rld.attach_hardware(self.camera, self.serial_connection)  # Attach hardware
        if self.rld.run() == -1: # button should be disabled if devices are not connected, but just in case
            # message box to inform user that acquisition failed
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Error: Image acquisition failed. Please check camera and controller connections.")
            msg.setWindowTitle("Error")
            msg.exec()
            return
        self.rld_list.append(self.rld)
        # add new measurement to combo box
        self.ui.select_measurement_cb.addItem(f"{len(self.rld_list)} (measured)")
        print(f"number of measurements in session: {len(self.rld_list)}")
        #change selection of combo box to the newly loaded measurement
        self.ui.select_measurement_cb.setCurrentIndex(len(self.rld_list)-1)
        if self.ui.auto_save_ckb.isChecked():
            # save images to cwd/timestamp folder with yyyy-mm-dd_hh-mm-ss format
            folder = time.strftime("%Y-%m-%d_%H-%M-%S")
            if not os.path.exists(folder):
                os.makedirs(folder)
            self.save_measurement(folder)
        self.plot_preview_images()   

    def load_measurement(self):
        #get image set folder from user. Open folder selection dialog
        data_folder_path = QFileDialog.getExistingDirectory(self, "Select Data Folder", os.getcwd())
        if not data_folder_path:
            return  # User cancelled    
        self.rld = RLD_manager.RLD() 
        load_image_status = self.rld.load_images_from_folder(data_folder_path)

        if load_image_status == 0:
           pass 
        elif not load_image_status:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Error: No valid image sets found in the selected folder.")
            msg.setWindowTitle("Warning")
            msg.exec()
            return
        elif load_image_status == -1:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Warning: Unequal number of window1, window2 and/or dark images in folder.")
            msg.setWindowTitle("Warning")
            msg.exec()

        config_path = next((os.path.join(data_folder_path, f) for f in os.listdir(data_folder_path) if f.endswith('.conf')), "")
        config_file_status = self.rld.load_settings_from_file(config_path)

        if config_file_status == 0:
            self.apply_settings_to_gui() 
        else:
            self.config_file_error_popup(config_file_status)

        self.rld.calculate_average_lifetime()

        self.rld_list.append(self.rld)
        # add new measurement to combo box
        self.ui.select_measurement_cb.addItem(f"{len(self.rld_list)} (loaded)")
        #change selection of combo box to the newly loaded measurement
        self.ui.select_measurement_cb.setCurrentIndex(len(self.rld_list)-1)
        print(f"number of measurements in session: {len(self.rld_list)}")

        self.plot_preview_images()

    def config_file_error_popup(self, config_file_status):
        if config_file_status == -1:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Incomplete settings in config file. Using default settings.")
            msg.setWindowTitle("Warning")
            msg.exec()
        elif config_file_status == -2:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("Corrupt config file. Settings left unchanged.")
            msg.setWindowTitle("Error")
            msg.exec()
        elif config_file_status is None:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Information)
            msg.setText("No .conf file found. Using default settings.")
            msg.setWindowTitle("Info")
            msg.exec()

    def save_lifetime_image(self):
        #todo: enable button only when there is a lifetime image to save
        if self.rld and self.rld.average_lifetime is not None:
            file_path, _ = QFileDialog.getSaveFileName(self, "Save Lifetime Image", "lifetime_image.tif", "TIFF Files (*.tif);;All Files (*)")
            if file_path:
                lifetime_img = self.rld.average_lifetime
                
                cv2.imwrite(file_path, lifetime_img)
        else:
            #pop up QMessageBox to inform user that there is no lifetime image to save
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("No decay time image to save.")
            msg.setWindowTitle("Warning")
            msg.exec()

    def save_measurement_folder_dialog(self):
        file_path = QFileDialog.getExistingDirectory(self, "Select Save Directory", os.getcwd())
        if not file_path:
            return  # User cancelled
        self.save_measurement(file_path)

    def save_measurement(self, file_path = None):
        #todo: enable button only when there are images to save

        #save raw images, average lifetime image, and settings.conf
        #probably better to move to RLD manager class later and just pass the file path here (also config file and timestamps)
        if self.rld and self.rld.image_dict and len(self.rld.image_dict.get("window1", [])) > 0 and len(self.rld.image_dict.get("window2", [])) > 0 and len(self.rld.image_dict.get("dark", [])) > 0:
            for i, img in enumerate(self.rld.image_dict.get("window1", [])):
                cv2.imwrite(os.path.join(file_path, f"window1_{i:03d}.tif"), img) 
            for i, img in enumerate(self.rld.image_dict.get("window2", [])):
                cv2.imwrite(os.path.join(file_path, f"window2_{i:03d}.tif"), img) 
            for i, img in enumerate(self.rld.image_dict.get("dark", [])):
                cv2.imwrite(os.path.join(file_path, f"dark_{i:03d}.tif"), img)
            if self.rld.average_lifetime is not None:
                cv2.imwrite(os.path.join(file_path, "lifetime_image.tif"), self.rld.average_lifetime)
        else:
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Warning)
            msg.setText("No images to save.")
            msg.setWindowTitle("Warning")
            msg.exec()
            return

        #save settings.conf
        #right now, if the settings are loaded from file AFTER acquiring images, the wrong settings are saved.
        config = configparser.ConfigParser()
        config['ImagingParameters'] = {
            'exposure_time_us': str(self.rld.params.exposure_time_us),
            'delay_window1_us': str(self.rld.params.delay_window1_us),
            'delay_window2_us': str(self.rld.params.delay_window2_us),
            'end_delay_us': str(self.rld.params.end_delay_us),
            'pulse_width_us': str(self.rld.params.pulse_width_us),
            'light_intensity': str(self.rld.params.light_intensity),
            'sets_to_acquire': str(self.rld.params.sets_to_acquire),
            'exposures_per_frame': str(self.rld.params.exposures_per_frame),
        }
        with open(os.path.join(file_path, 'settings.conf'), 'w') as configfile:
            config.write(configfile)


        if self.rld.start_time_ns and self.rld.end_time_ns:    # this is not guaranteed to be available if images were loaded from a folder
            #write timestamps to a text file
            with open(os.path.join(file_path, 'timestamps.txt'), 'w') as timestamps_file:
                #start time
                timestamps_file.write(f"Start timestamp: {self.rld.start_time_ns}\n")
                timestamps_file.write(f"End timestamp: {self.rld.end_time_ns}\n")
                timestamps_file.write(f"Start time: {time.strftime('%Y-%m-%d %H:%M:%S', self.rld.start_time_localtime)}:{self.rld.start_time_localtime_ms:.3f}\n")
                timestamps_file.write(f"End time: {time.strftime('%Y-%m-%d %H:%M:%S',self.rld.end_time_localtime)}:{self.rld.end_time_localtime_ms:.3f}\n")
                timestamps_file.write("Image acquisition timestamps (window, index, timestamp):\n")
                for key, ts_list in self.rld.image_start_time_dict.items():
                    for index, timestamp in enumerate(ts_list):
                        timestamps_file.write(f"{key}, {index}, {timestamp}\n")

    def change_measurement_selection(self):
        #change measurement to selection in combo box
        index = self.ui.select_measurement_cb.currentIndex()
        if 0 <= index < len(self.rld_list):
            self.rld = self.rld_list[index]
            self.apply_settings_to_gui()
            self.plot_preview_images()
    
    def clear_measurements(self):
        self.rld_list = []
        self.ui.select_measurement_cb.clear()
        self.rld = RLD_manager.RLD() 
        self.plot_preview_images()

    def clear_selected_measurement(self):
        index = self.ui.select_measurement_cb.currentIndex()
        if 0 <= index < len(self.rld_list):
            del self.rld_list[index]
            self.ui.select_measurement_cb.removeItem(index)
            if self.rld_list:
                new_index = min(index, len(self.rld_list) - 1)
                self.rld = self.rld_list[new_index]
                self.ui.select_measurement_cb.setCurrentIndex(new_index)
            else:
                self.rld = RLD_manager.RLD() 
            self.plot_preview_images()

    def load_settings(self):
        config_path, _ = QFileDialog.getOpenFileName(self, "Load Settings File", os.getcwd(), "Config Files (*.conf);;All Files (*)")
        if not config_path:
            return
        #perhaps best to create new RLD instance here to avoid conflicts with existing images/parameters... at least if the current instance contains images...
        config_file_status = self.rld.load_settings_from_file(config_path)
        if config_file_status == 0:
            self.apply_settings_to_gui() 
        else:
            self.config_file_error_popup(config_file_status)

    def save_settings(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Save Settings File", "settings.conf", "Config Files (*.conf);;All Files (*)")
        if not file_path:
            return
        self.extract_gui_inputs()
        config = configparser.ConfigParser()
        config['ImagingParameters'] = {
            'exposure_time_us': str(self.rld.params.exposure_time_us),
            'delay_window1_us': str(self.rld.params.delay_window1_us),
            'delay_window2_us': str(self.rld.params.delay_window2_us),
            'end_delay_us': str(self.rld.params.end_delay_us),
            'pulse_width_us': str(self.rld.params.pulse_width_us),
            'light_intensity': str(self.rld.params.light_intensity),
            'sets_to_acquire': str(self.rld.params.sets_to_acquire),
            'exposures_per_frame': str(self.rld.params.exposures_per_frame),
        }
        with open(file_path, 'w') as configfile:
            config.write(configfile)

#endregion

    def extract_gui_inputs(self):
        """Extract user inputs from GUI spinboxes and store them in an ImagingParameters instance."""
        params = ImagingParameters(
            exposure_time_us=self.ui.exposure_sb.value(),
            delay_window1_us=self.ui.delay1_sb.value(),
            delay_window2_us=self.ui.delay2_sb.value(),
            end_delay_us=self.ui.end_delay_sb.value(),
            pulse_width_us=self.ui.pulse_width_sb.value(),
            light_intensity=self.ui.light_intensity_sb.value(),
            sets_to_acquire=self.ui.sets_to_acquire_sb.value(),
            exposures_per_frame=self.ui.exposures_per_frame_sb.value(),
        )
        self.rld.params = params
        #print("Extracted parameters:", params)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    mainWindow = MainWindow()
    mainWindow.ui.show()  # Show the loaded UI
    sys.exit(app.exec())