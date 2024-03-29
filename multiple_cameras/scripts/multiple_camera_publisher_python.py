#!/usr/bin/env python3

from multiple_cameras.srv import ModifyCameraSettingsString, CameraId    
from multiple_cameras.msg import MultipleCameraStatus                                                        
from sensor_msgs.msg import CompressedImage

import rclpy
from rclpy.node import Node
import cv2
import numpy as np
from cv_bridge import CvBridge
import threading 
import time

class USBCamera:
    def __init__(self, dev_id):
        self.camera_settings = {
            "height": 720,
            "width": 1280,
            "raw_height": 720,
            "raw_width": 1280,
            "hardware_fps": 30,
            "supported_fps": [],
            "hardware_supports_fps_change": False,
            "running": False,
            "resize": False,
            "enable_publishing": False,
        }

        if isinstance(dev_id, str):
            self.cap = cv2.VideoCapture(dev_id)  # Should be something like '/dev/video0'
        elif isinstance(dev_id, int):
            self.cap = cv2.VideoCapture(dev_id*2)  # If Int multiply by two as USB cameras take up 2 video slots
        else:
            self.cap = None
        
        self.latest_image = None

        self.camera_read_thread = threading.Thread(target=self.camera_read_loop, daemon=True)
        self.read_lock = threading.Lock()

    def initialise_camera(self, config: dict):
        if self.cap is not None:
            if self.cap.isOpened():
                ret, self.latest_image = self.cap.read()
                
                if ret:
                    im_shape = self.latest_image.shape

                    self.camera_settings["height"] = config["height"] if "height" in config.keys() else im_shape[0]
                    self.camera_settings["width"] = config["width"] if "width" in config.keys() else im_shape[1]
                    self.camera_settings["raw_height"] = im_shape[0]
                    self.camera_settings["raw_width"] = im_shape[1]
                    self.camera_settings["hardware_fps"] = self.cap.get(cv2.CAP_PROP_FPS)
                    self.camera_settings["enable_publishing"] = config["enable_publishing"]

                    if self.camera_settings["height"] != im_shape[0] or self.camera_settings["width"] != im_shape[1]:
                        self.camera_settings["resize"] = True
                    else:
                        self.camera_settings["resize"] = False

                    if config["hardware_supports_fps_change"]:  # Not all USB cameras do
                        self.camera_settings["supported_fps"] = config["supported_fps"]
                        self.configure_hardware_fps(fps=config["fps"])

                    self.camera_settings["running"] = True
                    
            return self.camera_settings["running"] 

    def get_some(self):
        self.camera_read_thread.start()

    def camera_read_loop(self):
        while self.camera_settings["running"]:
            if self.camera_settings["enable_publishing"]:
                ret, frame = self.cap.read()

                if ret:
                    with self.read_lock:
                        if self.camera_settings["resize"]:
                            self.latest_image = cv2.resize(frame, (self.camera_settings["width"], self.camera_settings["height"]), interpolation=cv2.INTER_CUBIC)
                        else:
                            self.latest_image = frame.copy()
            else:
                time.sleep(0.05)
    
    def get_latest_image(self):
        with self.read_lock:
            if self.camera_settings["enable_publishing"]:
                valid = False if self.latest_image is None else True
                latest_image = self.latest_image.copy() if valid else None
            else:
                valid, latest_image = False, None

        return valid, latest_image

    def configure_hardware_fps(self, fps: int):
        fps_set = False

        if self.camera_settings["hardware_fps"] != fps:
            for supported_fps in self.camera_settings["supported_fps"]:
                if supported_fps >= fps and not fps_set:
                    self.cap.set(cv2.CAP_PROP_FPS, float(supported_fps))
                    self.camera_settings["hardware_fps"] = supported_fps
                    fps_set = True 

        if not fps_set:
            self.cap.set(cv2.CAP_PROP_FPS, float(self.camera_settings["supported_fps"][-1]))
            self.camera_settings["hardware_fps"] = self.camera_settings["supported_fps"][-1]

    def configure_image_size(self):
        if self.camera_settings["height"] != self.camera_settings["raw_height"] or self.camera_settings["width"] != self.camera_settings["raw_width"]:
            self.camera_settings["resize"] = True
        else:
            self.camera_settings["resize"] = False


class MultipleCameraPublisher(Node):
    def __init__(self):
        super().__init__('multiple_camera_node')
        self.modify_camera_service = self.create_service(ModifyCameraSettingsString, 
                                                         self.string_parameter("modify_camera_settings_service", "multiple_camera/modify_camera_settings"), 
                                                         self.modify_camera_settings_callback)       
        
        self.bridge = CvBridge()
        self.camera_id_client = self.create_client(CameraId, self.string_parameter("camera_id_service", "multiple_camera/get_camera_id"))   
        
        while not self.camera_id_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('Camera ID service not available, waiting again...')

        self.camera0_name = self.string_parameter("camera0/name", "camera0")
        self.camera0_exists = False

        if self.camera0_name.lower() != "none":
            if self.camera0_name == "camera0":
                self.camera0 = USBCamera(0)
            else:
                self.camera0 = USBCamera(self.get_camera_dev_id(self.camera0_name))

            self.camera0_fps = self.int_parameter("camera0/fps", 10)
            self.get_logger().info(f"Initialising camera 0 as {self.camera0_name}")

            if self.camera0.initialise_camera(config={
                "fps": self.camera0_fps,
                "height": self.int_parameter("camera0/height", 720),
                "width": self.int_parameter("camera0/width", 1080),
                "supported_fps": [],
                "hardware_supports_fps_change": False,
                "enable_publishing": True,
                }):

                self.camera0_publisher = self.create_publisher(CompressedImage, f"{self.camera0_name}/image/compressed", 10)  
                self.camera0_timer = self.create_timer(1/self.camera0_fps, self.camera0_timer_callback)
                self.get_logger().info("Camera 0 is ready....")
    
            else:
                self.get_logger().info("Camera 0 is dead....")

        else:
            self.camera0 = None
            self.get_logger().info("Camera 0 set to None")

        self.camera1_name = self.string_parameter("camera1/name", "camera1")
        self.camera1_exists = False
        
        if self.camera1_name.lower() != "none":
            if self.camera1_name == "camera1":
                self.camera1 = USBCamera(1)
            else:
                self.camera1 = USBCamera(self.get_camera_dev_id(self.camera1_name))

            self.camera1_fps = self.int_parameter("camera1/fps", 10)
            self.get_logger().info(f"Initialising camera 1 as {self.camera1_name}")
            
            if self.camera1.initialise_camera(config={
                "fps": self.camera1_fps,
                "height": self.int_parameter("camera1/height", 720),
                "width": self.int_parameter("camera1/width", 1080),
                "supported_fps": [],
                "hardware_supports_fps_change": False,
                "enable_publishing": True,
                }):

                self.camera1_publisher = self.create_publisher(CompressedImage, f"{self.camera1_name}/image/compressed", 10) 
                self.camera1_timer = self.create_timer(1/self.camera1_fps, self.camera1_timer_callback)
                self.get_logger().info("Camera 1 is ready....")
            
            else:
                self.get_logger().info("Camera 1 is dead....")
        
        else:
            self.camera1 = None
            self.get_logger().info("Camera 1 set to None")

        self.camera2_name = self.string_parameter("camera2/name", "camera2")
        self.camera2_exists = False

        if self.camera2_name.lower() != "none":
            if self.camera2_name == "camera2":
                self.camera2_exists = USBCamera(2)
            else:
                self.camera2 = USBCamera(self.get_camera_dev_id(self.camera2_name))

            self.camera2_fps = self.int_parameter("camera2/fps", 10)
            self.get_logger().info(f"Initialising camera 2 as {self.camera2_name}")

            if self.camera2.initialise_camera(config={
                "fps": self.camera2_fps,
                "height": self.int_parameter("camera2/height", 720),
                "width": self.int_parameter("camera2/width", 1080),            
                "supported_fps": [],
                "hardware_supports_fps_change": False,
                "enable_publishing": True,
                }):
                                        
                self.camera2_publisher = self.create_publisher(CompressedImage, f"{self.camera2_name}/image/compressed", 10) 
                self.camera2_timer = self.create_timer(1/self.camera2_fps, self.camera2_timer_callback)
                self.get_logger().info("Camera 2 is ready....")
                
            else:
                self.get_logger().info("Camera 2 is dead....")

        else:
            self.camera2 = None
            self.get_logger().info("Camera 2 set to None")

        self.camera3_name = self.string_parameter("camera3/name", "camera3")
        self.camera3_exists = False

        if self.camera3_name.lower() != "none":
            if self.camera3_name == "camera3":
                self.camera3_exists = USBCamera(3)
            else:
                self.camera3 = USBCamera(self.get_camera_dev_id(self.camera3_name))

            self.camera3_fps = self.int_parameter("camera3/fps", 10)
            self.get_logger().info(f"Initialising camera 3 as {self.camera3_name}")

            if self.camera3.initialise_camera(config={
                "fps": self.camera3_fps,
                "height": self.int_parameter("camera3/height", 720),
                "width": self.int_parameter("camera3/width", 1080),            
                "supported_fps": [],
                "hardware_supports_fps_change": False,
                "enable_publishing": True,
                }):

                self.camera3_publisher = self.create_publisher(CompressedImage, f"{self.camera3_name}/image/compressed", 10) 
                self.camera3_timer = self.create_timer(1/self.camera3_fps, self.camera3_timer_callback)
                self.get_logger().info("Camera 3 is ready....")
                
            else:
                self.get_logger().info("Camera 3 is dead....")

        else:
            self.camera3 = None
            self.get_logger().info("Camera 3 set to None")

        self.camera4_name = self.string_parameter("camera4/name", "camera4")
        self.camera4_exists = False

        if self.camera4_name.lower() != "none":
            if self.camera4_name == "camera4":
                self.camera4_exists = USBCamera(4)
            else:
                self.camera4 = USBCamera(self.get_camera_dev_id(self.camera4_name))

            self.camera4_fps = self.int_parameter("camera4/fps", 10)
            self.get_logger().info(f"Initialising camera 4 as {self.camera4_name}")

            if self.camera4.initialise_camera(config={
                "fps": self.camera4_fps,
                "height": self.int_parameter("camera4/height", 720),
                "width": self.int_parameter("camera4/width", 1080),            
                "supported_fps": [],
                "hardware_supports_fps_change": False,
                "enable_publishing": True,
                }):

                self.camera4_publisher = self.create_publisher(CompressedImage, f"{self.camera4_name}/image/compressed", 10) 
                self.camera4_timer = self.create_timer(1/self.camera4_fps, self.camera4_timer_callback)
                self.get_logger().info("Camera 4 is ready....")
                
            else:
                self.get_logger().info("Camera 4 is dead....")

        else:
            self.camera4 = None
            self.get_logger().info("Camera 4 set to None")

        self.camera5_name = self.string_parameter("camera5/name", "camera5")
        self.camera5_exists = False

        if self.camera5_name.lower() != "none":
            if self.camera5_name == "camera5":
                self.camera5_exists = USBCamera(5)
            else:
                self.camera5 = USBCamera(self.get_camera_dev_id(self.camera5_name))

            self.camera5_fps = self.int_parameter("camera5/fps", 10)
            self.get_logger().info(f"Initialising camera 5 as {self.camera5_name}")

            if self.camera5.initialise_camera(config={
                "fps": self.camera5_fps,
                "height": self.int_parameter("camera5/height", 720),
                "width": self.int_parameter("camera5/width", 1080),            
                "supported_fps": [],
                "hardware_supports_fps_change": False,
                "enable_publishing": True,
                }):

                self.camera5_publisher = self.create_publisher(CompressedImage, f"{self.camera5_name}/image/compressed", 10) 
                self.camera5_timer = self.create_timer(1/self.camera5_fps, self.camera5_timer_callback)
                self.get_logger().info("Camera 5 is ready....")
                
            else:
                self.get_logger().info("Camera 5 is dead....")
        else:
            self.camera5 = None
            self.get_logger().info("Camera 5 set to None")

        self.camera6_name = self.string_parameter("camera6/name", "camera6")
        self.camera6_exists = False

        if self.camera6_name.lower() != "none":
            if self.camera6_name == "camera6":
                self.camera6_exists = USBCamera(6)
            else:
                self.camera6 = USBCamera(self.get_camera_dev_id(self.camera6_name))

            self.camera6_fps = self.int_parameter("camera6/fps", 10)
            self.get_logger().info(f"Initialising camera 6 as {self.camera6_name}")

            if self.camera6.initialise_camera(config={
                "fps": self.camera6_fps,
                "height": self.int_parameter("camera6/height", 720),
                "width": self.int_parameter("camera6/width", 1080),            
                "supported_fps": [],
                "hardware_supports_fps_change": False,
                "enable_publishing": True,
                }):

                self.camera6_publisher = self.create_publisher(CompressedImage, f"{self.camera6_name}/image/compressed", 10) 
                self.camera6_timer = self.create_timer(1/self.camera6_fps, self.camera6_timer_callback)
                self.get_logger().info("Camera 6 is ready....")
                
            else:
                self.get_logger().info("Camera 6 is dead....")
        else:
            self.camera6 = None
            self.get_logger().info("Camera 6 set to None")

        self.camera7_name = self.string_parameter("camera7/name", "camera7")
        self.camera7_exists = False

        if self.camera7_name.lower() != "none":
            if self.camera7_name == "camera7":
                self.camera7_exists = USBCamera(7)
            else:
                self.camera7 = USBCamera(self.get_camera_dev_id(self.camera7_name))

            self.camera7_fps = self.int_parameter("camera7/fps", 10)
            self.get_logger().info(f"Initialising camera 7 as {self.camera7_name}")

            if self.camera7.initialise_camera(config={
                "fps": self.camera7_fps,
                "height": self.int_parameter("camera7/height", 720),
                "width": self.int_parameter("camera7/width", 1080),            
                "supported_fps": [],
                "hardware_supports_fps_change": False,
                "enable_publishing": True,
                }):

                self.camera7_publisher = self.create_publisher(CompressedImage, f"{self.camera7_name}/image/compressed", 10) 
                self.camera7_timer = self.create_timer(1/self.camera7_fps, self.camera7_timer_callback)
                self.get_logger().info("Camera 7 is ready....")
                
            else:
                self.get_logger().info("Camera 7 is dead....")
        else:
            self.camera7 = None
            self.get_logger().info("Camera 7 set to None")

        if self.camera0 is not None:
            if self.camera0.camera_settings["running"]:
                self.camera0.get_some()
                self.camera0_exists = True
            else:
                del self.camera0

        if self.camera1 is not None:
            if self.camera1.camera_settings["running"]:
                self.camera1.get_some()
                self.camera1_exists = True
            else:
                del self.camera1

        if self.camera2 is not None:
            if self.camera2.camera_settings["running"]:
                self.camera2.get_some()
                self.camera2_exists = True
            else:
                del self.camera2

        if self.camera3 is not None:
            if self.camera3.camera_settings["running"]:
                self.camera3.get_some()
                self.camera3_exists = True
            else:
                del self.camera3

        if self.camera4 is not None:
            if self.camera4.camera_settings["running"]:
                self.camera4.get_some()
                self.camera4_exists = True
            else:
                del self.camera4

        if self.camera5 is not None:
            if self.camera5.camera_settings["running"]:
                self.camera5.get_some()
                self.camera5_exists = True
            else:
                del self.camera5

        if self.camera6 is not None:
            if self.camera6.camera_settings["running"]:
                self.camera6.get_some()
                self.camera6_exists = True
            else:
                del self.camera6

        if self.camera7 is not None:
            if self.camera7.camera_settings["running"]:
                self.camera7.get_some()
                self.camera7_exists = True
            else:
                del self.camera7

        self.status_publisher = self.create_publisher(MultipleCameraStatus, 
                                                      self.string_parameter("multiple_camera_status_topic", "multiple_camera/status"), 
                                                      10)
        self.status_timer = self.create_timer(self.double_parameter("multiple_camera_status_message_hz", 1.0), self.status_timer_callback)

    def get_camera_dev_id(self, name: str):
        camera_id_req = CameraId.Request()      
        camera_id_req.camera_name = name
        future = self.camera_id_client.call_async(camera_id_req)
        
        while not future.done():
            rclpy.spin_once(self, timeout_sec=0.5)

        result: CameraId.Response = future.result()

        return result.dev_id

    def int_parameter(self, param_name: str, default_value: int):
        self.declare_parameter(param_name, default_value)
        
        try:
            updated_param = self.get_parameter(param_name).get_parameter_value().integer_value
     
        except Exception as e:
            self.get_logger().error(f"Unable to read parameter: {param_name}")
            updated_param = default_value
        
        return updated_param

    def string_parameter(self, param_name: str, default_value: str):
        self.declare_parameter(param_name, default_value)
        
        try:
            updated_param = self.get_parameter(param_name).get_parameter_value().string_value
        
        except Exception as e:
            self.get_logger().error(f"Unable to read parameter: {param_name}")
            updated_param = default_value
        
        return updated_param

    def double_parameter(self, param_name: str, default_value: float):
        self.declare_parameter(param_name, default_value)
        
        try:
            updated_param = self.get_parameter(param_name).get_parameter_value().double_value
        
        except Exception as e:
            self.get_logger().error(f"Unable to read parameter: {param_name}")
            updated_param = default_value
        
        return updated_param

    def modify_camera_settings_callback(self, request, response): 
        """
        string camera_id
        bool enable_publishing
        int64 height
        int64 width
        int64 fps
        ---
        bool success
        """
        if request.camera_id == self.camera0_name:
            if self.camera0_exists:
                self.camera0.camera_settings["enable_publishing"] = request.enable_publishing
                self.camera0.camera_settings["height"] = request.height
                self.camera0.camera_settings["width"] = request.width
                self.camera0.configure_image_size()

                self.get_logger().info(f"Camera {self.camera0_name} (h {request.height}, w {request.width}), publish {request.enable_publishing}")

                if self.camera0_fps != request.fps and 0 < request.fps <= 30:
                    self.camera0_fps = request.fps
                    self.camera0_timer.timer_period_ns = 1/self.camera0_fps * 1e9
                    self.get_logger().info(f"Camera {self.camera0_name} set to {self.camera0_fps} FPS")

                    if self.camera0.camera_settings["hardware_supports_fps_change"]:
                        self.camera0.configure_hardware_fps(fps=request.fps)
            
                response.success = True
            
            else:
                response.success = False

        elif request.camera_id == self.camera1_name:
            if self.camera1_exists:
                self.camera1.camera_settings["enable_publishing"] = request.enable_publishing
                self.camera1.camera_settings["height"] = request.height
                self.camera1.camera_settings["width"] = request.width
                self.camera1.configure_image_size()
                
                self.get_logger().info(f"Camera {self.camera1_name} (h {request.height}, w {request.width}), publish {request.enable_publishing}")
                
                if self.camera1_fps != request.fps and 0 < request.fps <= 30:
                    self.camera1_fps = request.fps
                    self.camera1_timer.timer_period_ns = 1/self.camera1_fps * 1e9
                    self.get_logger().info(f"Camera {self.camera1_name} set to {self.camera1_fps} FPS")

                    if self.camera1.camera_settings["hardware_supports_fps_change"]:
                        self.camera1.configure_hardware_fps(fps=request.fps)
                
                response.success = True

            else:
                response.success = False

        elif request.camera_id == self.camera2_name:
            if self.camera2_exists:
                self.camera2.camera_settings["enable_publishing"] = request.enable_publishing
                self.camera2.camera_settings["height"] = request.height
                self.camera2.camera_settings["width"] = request.width
                self.camera2.configure_image_size()
                
                self.get_logger().info(f"Camera {self.camera2_name} (h {request.height}, w {request.width}), publish {request.enable_publishing}")

                if self.camera2_fps != request.fps and 0 < request.fps <= 30:
                    self.camera2_fps = request.fps
                    self.camera2_timer.timer_period_ns = 1/self.camera2_fps * 1e9
                    self.get_logger().info(f"Camera {self.camera2_name} set to {self.camera2_fps} FPS")

                    if self.camera2.camera_settings["hardware_supports_fps_change"]:
                        self.camera2.configure_hardware_fps(fps=request.fps)
                
                response.success = True

            else:
                response.success = False

        elif request.camera_id == self.camera3_name:
            if self.camera3_exists:
                self.camera3.camera_settings["enable_publishing"] = request.enable_publishing
                self.camera3.camera_settings["height"] = request.height
                self.camera3.camera_settings["width"] = request.width
                self.camera3.configure_image_size()

                self.get_logger().info(f"Camera {self.camera3_name} (h {request.height}, w {request.width}), publish {request.enable_publishing}")

                if self.camera3_fps != request.fps and 0 < request.fps <= 30:
                    self.camera3_fps = request.fps
                    self.camera3_timer.timer_period_ns = 1/self.camera3_fps * 1e9
                    self.get_logger().info(f"Camera {self.camera3_name} set to {self.camera3_fps} FPS")

                    if self.camera3.camera_settings["hardware_supports_fps_change"]:
                        self.camera3.configure_hardware_fps(fps=request.fps)
                
                response.success = True

            else:
                response.success = False

        elif request.camera_id == self.camera4_name:
            if self.camera4_exists:
                self.camera4.camera_settings["enable_publishing"] = request.enable_publishing
                self.camera4.camera_settings["height"] = request.height
                self.camera4.camera_settings["width"] = request.width
                self.camera4.configure_image_size()

                self.get_logger().info(f"Camera {self.camera4_name} (h {request.height}, w {request.width}), publish {request.enable_publishing}")

                if self.camera4_fps != request.fps and 0 < request.fps <= 30:
                    self.camera4_fps = request.fps
                    self.camera4_timer.timer_period_ns = 1/self.camera4_fps * 1e9
                    self.get_logger().info(f"Camera {self.camera4_name} set to {self.camera4_fps} FPS")

                    if self.camera4.camera_settings["hardware_supports_fps_change"]:
                        self.camera4.configure_hardware_fps(fps=request.fps)
                
                response.success = True

            else:
                response.success = False
        
        elif request.camera_id == self.camera5_name:
            if self.camera5_exists:
                self.camera5.camera_settings["enable_publishing"] = request.enable_publishing
                self.camera5.camera_settings["height"] = request.height
                self.camera5.camera_settings["width"] = request.width
                self.camera5.configure_image_size()

                self.get_logger().info(f"Camera {self.camera5_name} (h {request.height}, w {request.width}), publish {request.enable_publishing}")

                if self.camera5_fps != request.fps and 0 < request.fps <= 30:
                    self.camera5_fps = request.fps
                    self.camera5_timer.timer_period_ns = 1/self.camera5_fps * 1e9
                    self.get_logger().info(f"Camera {self.camera5_name} set to {self.camera5_fps} FPS")

                    if self.camera5.camera_settings["hardware_supports_fps_change"]:
                        self.camera5.configure_hardware_fps(fps=request.fps)
                
                response.success = True

            else:
                response.success = False

        elif request.camera_id == self.camera6_name:
            if self.camera6_exists:
                self.camera6.camera_settings["enable_publishing"] = request.enable_publishing
                self.camera6.camera_settings["height"] = request.height
                self.camera6.camera_settings["width"] = request.width
                self.camera6.configure_image_size()

                self.get_logger().info(f"Camera {self.camera6_name} (h {request.height}, w {request.width}), publish {request.enable_publishing}")

                if self.camera6_fps != request.fps and 0 < request.fps <= 30:
                    self.camera6_fps = request.fps
                    self.camera6_timer.timer_period_ns = 1/self.camera6_fps * 1e9
                    self.get_logger().info(f"Camera {self.camera6_name} set to {self.camera6_fps} FPS")

                    if self.camera6.camera_settings["hardware_supports_fps_change"]:
                        self.camera6.configure_hardware_fps(fps=request.fps)
                
                response.success = True

            else:
                response.success = False

        elif request.camera_id == self.camera7_name:
            if self.camera7_exists:
                self.camera7.camera_settings["enable_publishing"] = request.enable_publishing
                self.camera7.camera_settings["height"] = request.height
                self.camera7.camera_settings["width"] = request.width
                self.camera7.configure_image_size()

                self.get_logger().info(f"Camera {self.camera7_name} (h {request.height}, w {request.width}), publish {request.enable_publishing}")

                if self.camera7_fps != request.fps and 0 < request.fps <= 30:
                    self.camera7_fps = request.fps
                    self.camera7_timer.timer_period_ns = 1/self.camera7_fps * 1e9
                    self.get_logger().info(f"Camera {self.camera7_name} set to {self.camera7_fps} FPS")

                    if self.camera7.camera_settings["hardware_supports_fps_change"]:
                        self.camera7.configure_hardware_fps(fps=request.fps)
                
                response.success = True

            else:
                response.success = False

        else:
            response.success = False
        
        return response
    
    def status_timer_callback(self):
        sts_msg = MultipleCameraStatus()

        if self.camera0_exists:
            sts_msg.camera_0.opened = self.camera0.camera_settings["running"]
            sts_msg.camera_0.enable_publishing = self.camera0.camera_settings["enable_publishing"]
            sts_msg.camera_0.height = self.camera0.camera_settings["height"]
            sts_msg.camera_0.width = self.camera0.camera_settings["width"]
            sts_msg.camera_0.fps = self.camera0_fps

        if self.camera1_exists:
            sts_msg.camera_1.opened = self.camera1.camera_settings["running"]
            sts_msg.camera_1.enable_publishing = self.camera1.camera_settings["enable_publishing"]
            sts_msg.camera_1.height = self.camera1.camera_settings["height"]
            sts_msg.camera_1.width = self.camera1.camera_settings["width"]
            sts_msg.camera_1.fps = self.camera1_fps
        
        if self.camera2_exists:
            sts_msg.camera_2.opened = self.camera2.camera_settings["running"]
            sts_msg.camera_2.enable_publishing = self.camera2.camera_settings["enable_publishing"]
            sts_msg.camera_2.height = self.camera2.camera_settings["height"]
            sts_msg.camera_2.width = self.camera2.camera_settings["width"]
            sts_msg.camera_2.fps = self.camera2_fps

        if self.camera3_exists:
            sts_msg.camera_3.opened = self.camera3.camera_settings["running"]
            sts_msg.camera_3.enable_publishing = self.camera3.camera_settings["enable_publishing"]
            sts_msg.camera_3.height = self.camera3.camera_settings["height"]
            sts_msg.camera_3.width = self.camera3.camera_settings["width"]
            sts_msg.camera_3.fps = self.camera3_fps

        if self.camera4_exists:
            sts_msg.camera_4.opened = self.camera4.camera_settings["running"]
            sts_msg.camera_4.enable_publishing = self.camera4.camera_settings["enable_publishing"]
            sts_msg.camera_4.height = self.camera4.camera_settings["height"]
            sts_msg.camera_4.width = self.camera4.camera_settings["width"]
            sts_msg.camera_4.fps = self.camera4_fps
        
        if self.camera5_exists:
            sts_msg.camera_5.opened = self.camera5.camera_settings["running"]
            sts_msg.camera_5.enable_publishing = self.camera5.camera_settings["enable_publishing"]
            sts_msg.camera_5.height = self.camera5.camera_settings["height"]
            sts_msg.camera_5.width = self.camera5.camera_settings["width"]
            sts_msg.camera_5.fps = self.camera5_fps

        if self.camera6_exists:
            sts_msg.camera_6.opened = self.camera6.camera_settings["running"]
            sts_msg.camera_6.enable_publishing = self.camera6.camera_settings["enable_publishing"]
            sts_msg.camera_6.height = self.camera6.camera_settings["height"]
            sts_msg.camera_6.width = self.camera6.camera_settings["width"]
            sts_msg.camera_6.fps = self.camera6_fps

        if self.camera7_exists:
            sts_msg.camera_7.opened = self.camera7.camera_settings["running"]
            sts_msg.camera_7.enable_publishing = self.camera7.camera_settings["enable_publishing"]
            sts_msg.camera_7.height = self.camera7.camera_settings["height"]
            sts_msg.camera_7.width = self.camera7.camera_settings["width"]
            sts_msg.camera_7.fps = self.camera7_fps

        self.status_publisher.publish(sts_msg)

    def camera0_timer_callback(self):
        valid, frame = self.camera0.get_latest_image()
        
        if valid:
            img_msg = self.bridge.cv2_to_compressed_imgmsg(frame)
            self.camera0_publisher.publish(img_msg)

    def camera1_timer_callback(self):
        valid, frame = self.camera1.get_latest_image()
            
        if valid:              
            img_msg = self.bridge.cv2_to_compressed_imgmsg(frame)
            self.camera1_publisher.publish(img_msg)

    def camera2_timer_callback(self):
        valid, frame = self.camera2.get_latest_image()
            
        if valid:              
            img_msg = self.bridge.cv2_to_compressed_imgmsg(frame)
            self.camera2_publisher.publish(img_msg)

    def camera3_timer_callback(self):
        valid, frame = self.camera3.get_latest_image()
            
        if valid:              
            img_msg = self.bridge.cv2_to_compressed_imgmsg(frame)
            self.camera3_publisher.publish(img_msg)            

    def camera4_timer_callback(self):
        valid, frame = self.camera4.get_latest_image()
            
        if valid:              
            img_msg = self.bridge.cv2_to_compressed_imgmsg(frame)
            self.camera4_publisher.publish(img_msg)    

    def camera5_timer_callback(self):
        valid, frame = self.camera5.get_latest_image()
            
        if valid:              
            img_msg = self.bridge.cv2_to_compressed_imgmsg(frame)
            self.camera5_publisher.publish(img_msg)    

    def camera6_timer_callback(self):
        valid, frame = self.camera6.get_latest_image()
            
        if valid:              
            img_msg = self.bridge.cv2_to_compressed_imgmsg(frame)
            self.camera6_publisher.publish(img_msg)  
             
    def camera7_timer_callback(self):
        valid, frame = self.camera7.get_latest_image()
            
        if valid:              
            img_msg = self.bridge.cv2_to_compressed_imgmsg(frame)
            self.camera7_publisher.publish(img_msg)     


def main(args=None):
    rclpy.init(args=args)
    multiple_camera_publisher = MultipleCameraPublisher()
    rclpy.spin(multiple_camera_publisher)
    multiple_camera_publisher.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()