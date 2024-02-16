from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package="multiple_cameras",
            executable="compressed_to_image_publisher",
            name="multiple_camera_compressed_image_subscriber",
            output="screen",
            emulate_tty=True,
            parameters=[
                {"camera0_compressed_topic": "camera0/image/compressed", 
                 "camera0_topic": "camera0/image/decompressed", 
                 "camera1_compressed_topic": "camera1/image/compressed",
                 "camera1_topic": "camera1/image/decompressed", 
                 "camera2_compressed_topic": "camera2/image/compressed",
                 "camera2_topic": "camera2/image/decompressed", 
                 "camera3_compressed_topic": "camera3/image/compressed",
                 "camera3_topic": "camera3/image/decompressed", 
                 }
            ]
        )
    ])