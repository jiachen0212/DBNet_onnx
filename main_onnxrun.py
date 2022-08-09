# coding=utf-8

import onnxruntime as ort
import numpy as np
import argparse
import cv2
import pyclipper
from shapely.geometry import Polygon

class SegDetectorRepresenter():
    def __init__(self, thresh=0.5, box_thresh=0.7, max_candidates=1000, unclip_ratio=1.5):
        self.min_size = 3
        self.thresh = thresh
        self.box_thresh = box_thresh
        self.max_candidates = max_candidates
        self.unclip_ratio = unclip_ratio
    def __call__(self, batch, pred, is_output_polygon=False):
        segmentation = self.binarize(pred)
        boxes_batch = []
        scores_batch = []
        height, width = batch['shape']
        if is_output_polygon:
            boxes, scores = self.polygons_from_bitmap(pred, segmentation, width, height)
        else:
            boxes, scores = self.boxes_from_bitmap(pred, segmentation, width, height)
        boxes_batch.append(boxes)
        scores_batch.append(scores)

        return boxes_batch, scores_batch

    # 二值化 
    def binarize(self, pred):
        return pred > self.thresh

    # polygons多边形
    def polygons_from_bitmap(self, pred, bitmap, dest_width, dest_height):
        assert len(bitmap.shape) == 2
        height, width = bitmap.shape
        boxes = []
        scores = []
        contours, _ = cv2.findContours((bitmap * 255).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours[:self.max_candidates]:
            # cv2.arcLength: 计算轮廓的周长, True表示轮廓闭合, False表示曲线,即轮廓开放
            epsilon = 0.005 * cv2.arcLength(contour, True)
            # cv2.approxPolyDP多边形逼近, approx为逼近拟合得到的多边形,[文本的话, 至少得是4边形. line:49]
            # epsilon==轮廓周长*0.005, 就变成一个自适应的参数了
            # True表示轮廓闭合, False表示曲线,即轮廓开放
            approx = cv2.approxPolyDP(contour, epsilon, True)
            points = approx.reshape((-1, 2))
            # 文本的话, 至少得是4边形
            if points.shape[0] < 4:
                continue
            # _, sside = self.get_mini_boxes(contour)
            # if sside < self.min_size:
            #     continue
            score = self.box_score_fast(pred, contour.squeeze(1))
            if self.box_thresh > score:
                continue

            if points.shape[0] > 2:
                box = self.unclip(points, unclip_ratio=self.unclip_ratio)
                if len(box) > 1:
                    continue
            else:
                continue
            box = box.reshape(-1, 2)
            _, sside = self.get_mini_boxes(box.reshape((-1, 1, 2)))
            if sside < self.min_size + 2:
                continue

            if not isinstance(dest_width, int):
                dest_width = dest_width.item()
                dest_height = dest_height.item()

            box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_width), 0, dest_width)
            box[:, 1] = np.clip(np.round(box[:, 1] / height * dest_height), 0, dest_height)
            boxes.append(box)
            scores.append(score)

        return boxes, scores

    def boxes_from_bitmap(self, pred, bitmap, dest_width, dest_height):
        assert len(bitmap.shape) == 2
        height, width = bitmap.shape
        contours, _ = cv2.findContours((bitmap * 255).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        num_contours = min(len(contours), self.max_candidates)
        boxes = np.zeros((num_contours, 4, 2), dtype=np.int16)
        scores = np.zeros((num_contours,), dtype=np.float32)

        for index in range(num_contours):
            contour = contours[index].squeeze(1)
            points, sside = self.get_mini_boxes(contour)
            if sside < self.min_size:
                continue
            points = np.array(points)
            score = self.box_score_fast(pred, contour)
            if self.box_thresh > score:
                continue

            box = self.unclip(points, unclip_ratio=self.unclip_ratio).reshape(-1, 1, 2)
            box, sside = self.get_mini_boxes(box)
            if sside < self.min_size + 2:
                continue
            box = np.array(box)
            if not isinstance(dest_width, int):
                dest_width = dest_width.item()
                dest_height = dest_height.item()

            box[:, 0] = np.clip(np.round(box[:, 0] / width * dest_width), 0, dest_width)
            box[:, 1] = np.clip(np.round(box[:, 1] / height * dest_height), 0, dest_height)
            boxes[index, :, :] = box.astype(np.int16)
            scores[index] = score
        return boxes, scores

    def unclip(self, box, unclip_ratio=1.5):
        poly = Polygon(box)
        distance = poly.area * unclip_ratio / poly.length
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(box, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        expanded = np.array(offset.Execute(distance))

        return expanded

    def get_mini_boxes(self, contour):
        bounding_box = cv2.minAreaRect(contour)
        points = sorted(list(cv2.boxPoints(bounding_box)), key=lambda x: x[0])
        if points[1][1] > points[0][1]:
            index_1 = 0
            index_4 = 1
        else:
            index_1 = 1
            index_4 = 0
        if points[3][1] > points[2][1]:
            index_2 = 2
            index_3 = 3
        else:
            index_2 = 3
            index_3 = 2
        box = [points[index_1], points[index_2], points[index_3], points[index_4]]

        return box, min(bounding_box[1])

    def box_score_fast(self, bitmap, _box):
        h, w = bitmap.shape[:2]
        box = _box.copy()
        xmin = np.clip(np.floor(box[:, 0].min()).astype(np.int), 0, w - 1)
        xmax = np.clip(np.ceil(box[:, 0].max()).astype(np.int), 0, w - 1)
        ymin = np.clip(np.floor(box[:, 1].min()).astype(np.int), 0, h - 1)
        ymax = np.clip(np.ceil(box[:, 1].max()).astype(np.int), 0, h - 1)

        mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
        box[:, 0] = box[:, 0] - xmin
        box[:, 1] = box[:, 1] - ymin
        cv2.fillPoly(mask, box.reshape(1, -1, 2).astype(np.int32), 1)
        
        return cv2.mean(bitmap[ymin:ymax + 1, xmin:xmax + 1], mask)[0]

class dbnet:
    def __init__(self, binaryThreshold=0.5, polygonThreshold=0.7, unclipRatio=1.5, maxCandidates=1000):
        self.model = ort.InferenceSession('dbnet.onnx')
        # DBNet算法inference
        self.decode = SegDetectorRepresenter(thresh=binaryThreshold, box_thresh=polygonThreshold, max_candidates=maxCandidates, unclip_ratio=unclipRatio)
        self.mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape((1, 1, 3))
        self.std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape((1, 1, 3))
        self.imgsize = (736, 736)
    def detect(self, srcimg):
        h, w = srcimg.shape[:2]
        img = cv2.cvtColor(srcimg, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, self.imgsize)
        img = img.astype(np.float32) / 255.0
        img = (img - self.mean) / self.std
        img = np.expand_dims(np.transpose(img, (2, 0, 1)), axis=0).astype(np.float32)

        outputs = self.model.run(None, {'input': img})
        mask = outputs[0][0, 0, ...]
        batch = {'shape': (h, w)}
        box_list, score_list = self.decode(batch, outputs[0])
        box_list, score_list = box_list[0], score_list[0]
        is_output_polygon = False
        if len(box_list) > 0:
            if is_output_polygon:
                idx = [x.sum() > 0 for x in box_list]
                box_list = [box_list[i] for i, v in enumerate(idx) if v]
                score_list = [score_list[i] for i, v in enumerate(idx) if v]
            else:
                idx = box_list.reshape(box_list.shape[0], -1).sum(axis=1) > 0  # 去掉全为0的框
                box_list, score_list = box_list[idx], score_list[idx]
        else:
            box_list, score_list = [], []
        for point in box_list:
            point = point.astype(int)
            cv2.polylines(srcimg, [point], True, (0, 0, 255), thickness=2)
            for i in range(4):
                cv2.circle(srcimg, tuple(point[i, :]), 3, (0, 255, 0), thickness=-1)

        return srcimg

def cmp_onnxrun_opencv(imgpath):
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape((1, 1, 3))
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape((1, 1, 3))
    imgsize = (736, 736)
    # onnx_runtime和cv2.dnn对比. 精度是否能对齐.
    onnx_model = ort.InferenceSession('dbnet.onnx')
    opencv_model = cv2.dnn.readNet('dbnet.onnx')

    srcimg = cv2.imread(imgpath)
    img = cv2.cvtColor(srcimg, cv2.COLOR_BGR2RGB)
    img = cv2.resize(img, imgsize)
    img = img.astype(np.float32) / 255.0
    img = (img - mean) / std

    onnx_blob = np.expand_dims(np.transpose(img, (2, 0, 1)), axis=0).astype(np.float32)
    onnx_out = onnx_model.run(None, {'input': onnx_blob})

    opencv_blob = cv2.dnn.blobFromImage(img)
    opencv_model.setInput(opencv_blob)
    opencv_out = opencv_model.forward()

    if np.array_equal(onnx_blob, opencv_blob):
        print('input is same')
    else:
        print('input is different, mean dif =', np.mean(onnx_blob - opencv_blob))
    if np.array_equal(onnx_out, opencv_out):
        print('output is same')
    else:
        print('output is different, mean dif =', np.mean(onnx_out - opencv_out))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='RetinaPL')
    parser.add_argument('--imgpath', default='testimgs/1000.jpg', type=str, help='image path')
    parser.add_argument('--binaryThreshold', default=0.5, type=float, help='binary Threshold')
    parser.add_argument('--polygonThreshold', default=0.7, type=float, help='polygon Threshold')
    parser.add_argument('--unclipRatio', default=1.7, type=float, help='unclip Ratio')
    parser.add_argument('--maxCandidates', default=1000, type=int, help='max Candidates')
    args = parser.parse_args()
    # onnx_runtime和cv2.dnn精度对齐对比
    cmp_onnxrun_opencv(args.imgpath)
    net = dbnet(binaryThreshold=args.binaryThreshold, polygonThreshold=args.polygonThreshold,
                unclipRatio=args.unclipRatio, maxCandidates=args.maxCandidates)
    srcimg = cv2.imread(args.imgpath)
    srcimg = net.detect(srcimg)
    # cv2.imwrite('result.jpg', srcimg)
    cv2.namedWindow('detect', cv2.WINDOW_NORMAL)
    cv2.imshow('detect', srcimg)
    cv2.waitKey(0)
    cv2.destroyAllWindows()