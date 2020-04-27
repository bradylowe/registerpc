
from registerpc.pointcloud.PointCloud import PointCloud
from registerpc.pointcloud.Voxelize import VoxelGrid
from registerpc.label_file import LabelFile, LabelFileError
from tqdm import tqdm
from qtpy import QtCore, QtGui
import numpy as np
from collections import defaultdict
import qimage2ndarray


class Room:

    colors = ['black', 'red', 'blue', 'green', 'cyan', 'magenta']
    colors += colors[1:]

    def __init__(self, filename, index, mesh, thickness, max_points=None):
        filename = str(filename)
        if max_points is None:
            max_points = 1000000000
        if filename.lower().endswith('.json'):
            self.filename = LabelFile.get_source(filename)
            self.labelFilename = filename
        else:
            self.filename = filename
            self.labelFilename = '.'.join(self.filename.split('.')[:-1] + ['json'])
        self.mesh, self.thickness = mesh, thickness
        self.pointcloud = PointCloud(self.filename, max_points=max_points, render=False)
        self.annotations = LabelFile(self.labelFilename)
        self.index = index
        self.min_point, self.max_point, self.min_idx, self.max_idx = None, None, None, None
        self.calculateMinMax()
        self.min_slice, self.max_slice = int(self.min_point[2] / thickness) - 1, int(self.max_point[2] / thickness)
        self.slices = defaultdict(list)
        for idx in range(self.min_slice, self.max_slice + 1):
            vals = self.pointcloud.points['z']
            keep = np.where(np.logical_and(idx * self.thickness <= vals, vals < (idx + 1) * self.thickness))
            self.slices[idx] = self.pointcloud.points.index[keep].tolist()
        self.images = defaultdict(QtGui.QImage)
        self.buildImages()

    def calculateMinMax(self, idx=None):
        if idx is None:
            points = self.pointcloud.points[['x', 'y', 'z']].values
        else:
            points = self.slice(idx)
        self.min_point, self.max_point = points.min(axis=0), points.max(axis=0)
        self.min_idx, self.max_idx = (self.min_point / self.mesh).astype(int), (self.max_point / self.mesh).astype(int)

    def buildImages(self):
        for idx in range(self.min_slice, self.max_slice + 1):
            self.buildImage(idx)

    def rotate(self, angle, center=None, idx=None):
        if center is None:
            center = self.center
        self.rotate_annotations(angle, center)
        self.rotate_points(angle, center, idx)

    def rotate_annotations(self, angle, center):
        pass

    def rotate_points(self, angle, center, idx=None):
        angle = np.radians(angle)
        c, s = np.cos(angle), np.sin(angle)
        rot = np.array(((c, s), (-s, c)))
        if idx is None:
            self.pointcloud.points[['x', 'y']] -= center[:2]
            self.pointcloud.points[['x', 'y']] = np.dot(self.pointcloud.points[['x', 'y']].values, rot)
            self.pointcloud.points[['x', 'y']] += center[:2]
            self.calculateMinMax()
            self.buildImages()
        else:
            indices = self.slices[idx]
            self.pointcloud.points.iloc[indices][['x', 'y']] -= center[:2]
            self.pointcloud.points.iloc[indices][['x', 'y']] = np.dot(self.pointcloud.points.iloc[indices][['x', 'y']].values, rot)
            self.pointcloud.points.iloc[indices][['x', 'y']] += center[:2]
            self.calculateMinMax(idx)
            self.buildImage(idx)

    def translate(self, delta, idx=None):
        self.translate_annotations(delta)
        self.translate_points(delta, idx)

    def translate_annotations(self, delta):
        pass

    def translate_points(self, delta, idx=None):
        if idx is None:
            self.pointcloud.points[['x', 'y']] += delta[:2]
            self.calculateMinMax()
        else:
            self.pointcloud.points.loc[self.slices[idx]][['x', 'y']] += delta[:2]
            self.min_point, self.max_point = self.min_point + delta, self.max_point + delta
            self.min_idx, self.max_idx = (self.min_point / self.mesh).astype(int), (self.max_point / self.mesh).astype(int)

    def slice(self, idx):
        return self.pointcloud.points.iloc[self.slices[idx]][['x', 'y', 'z']].values

    def save(self, pointFile=None, labelFile=None, shapes=None, otherData=None):
        if pointFile is None:
            pointFile = self.pointcloud.filename
        if labelFile is None:
            labelFile = self.labelFilename
        if shapes is None:
            shapes = []
        self.pointcloud.write(pointFile, overwrite=True)
        self.annotations.save(labelFile, shapes, pointFile, otherData)

    def buildImage(self, idx):
        vg = VoxelGrid(self.slice(idx), (self.mesh, self.mesh, 100000.), (0.0, 0.0, -10000.))
        bitmap = vg.bitmapFromSlice(max=255, min_idx=self.min_idx, max_idx=self.max_idx, color=self.colors[self.index])
        image = qimage2ndarray.array2qimage(bitmap)
        self.images[idx] = image

    @property
    def points(self):
        return self.pointcloud.points[['x', 'y', 'z']].values

    @property
    def center(self):
        return self.points.mean(axis=0)
