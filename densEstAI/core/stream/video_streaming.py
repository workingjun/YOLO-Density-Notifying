import os
import cv2
import time
import queue
import threading
from ocsort import OCSort
from densEstAI.core.yolo.yolo_manager import YoloManager
from densEstAI.core.analy.density_plotter import LivePlotter
from densEstAI.core.analy.density_estimation import DensityEstimator
from densEstAI.core.utils.tracking import tracking_object
from densEstAI.core.utils.drawing_boxes import draw_tracking_boxes
from densEstAI.core.utils.video_manager import BaseVideoCap, BaseVideoWriter
from densEstAI.utils.common import detect_display


class BaseVideoStreamer:
    
    scale = 1
    output_dir = "./results/predict/"
    os.makedirs(output_dir, exist_ok=True)
    resize_width = 960
    resize_height = 540

    def __init__(self, video_path, model_path, output_name, camera_height):
        self.frame_id = 0
        self.track_hist = []

        self.video_cap = BaseVideoCap()
        self.cap, video_fps, frame_width, frame_height = self.video_cap.init_cap(video_path)

        self.video_writer = BaseVideoWriter()
        self.video_writer.fps = video_fps
        self.video_writer.init_writer(frame_width, frame_height, self.output_dir + output_name)

        self.tracker = OCSort(det_thresh=0.3, max_age=30, min_hits=3)
        self.model = YoloManager(model_path)
        self.plotter = LivePlotter()
        self.estimator = DensityEstimator(camera_height, frame_height)
    
class SingleThreadStreamer(BaseVideoStreamer):
    def __init__(self, video_path, model_path, output_name, camera_height):
        super().__init__(video_path, model_path, output_name, camera_height)

    def start_stream(self):
        while self.cap.isOpened():
            ret, frame = self.cap.read()
            self.frame_id += 1
            if not ret:
                break
            results = self.model.smart_predict_yolo(frame=frame, conf=0.5, save=False, half=True, stream=False)
            tracked_objects = tracking_object(self.tracker, results, self.frame_id)
            density = self.estimator.calculate_density(results)
            plot = draw_tracking_boxes(frame, tracked_objects)  # Bounding box 그리기
            self.plotter.update_live_density(density)
            self.video_writer.write(plot)
            resize_plot = cv2.resize(plot, (self.resize_width, self.resize_height))
            cv2.imshow("YOLO Stream", resize_plot)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    def stop_stream(self):
        self.video_cap.close_cap()
        self.video_writer.close_writer()

class ThreadedVideoStreamer(BaseVideoStreamer):
    def __init__(self, video_path, model_path, output_name, camera_height):
        super().__init__(video_path, model_path, output_name, camera_height)
        self.thread = None
        self.running = False
        self.graph_queue = queue.Queue()  

    def start_stream(self):
        self.running = True

        def run():
            while self.cap.isOpened() and self.running:
                ret, frame = self.cap.read()
                self.frame_id += 1
                if not ret:
                    break
                results = self.model.smart_predict_yolo(frame=frame, conf=0.5, save=False, half=True, stream=False)
                tracked_objects = tracking_object(self.tracker, results, self.frame_id)
                density = self.estimator.calculate_density(results)
                plot = draw_tracking_boxes(frame, tracked_objects)  
                self.graph_queue.put(density)   
                self.video_writer.write(plot)
                resize_plot = cv2.resize(plot, (self.resize_width, self.resize_height))
                cv2.imshow("YOLO Stream", resize_plot)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    self.running = False
                    break

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

        while self.thread.is_alive():
            self.process_graph_queue()
            time.sleep(0.05)

    def process_graph_queue(self):
        try:
            while True:
                density = self.graph_queue.get_nowait()
                self.plotter.update_live_density(density)
        except queue.Empty:
            pass

    def stop_stream(self):
        self.running = False
        if self.thread is not None:
            self.thread.join()
            
        # 그래프 큐 비우기
        while not self.graph_queue.empty():
            try:
                self.graph_queue.get_nowait()
            except queue.Empty:
                break

        if self.cap.isOpened():
            self.video_cap.close_cap()
        if self.video_writer is not None:
            self.video_writer.close_writer()


