
from registerpc.pointcloud.PointCloud import PointCloud
from registerpc.label_file import LabelFile, LabelFileError
import os.path as osp
from qtpy import QtCore
import numpy as np
from collections import defaultdict


class Room:

    def __init__(self, filename, thickness, max_points=None):
        filename = str(filename)
        if max_points is None:
            max_points = 1000000000
        if filename.lower().endswith('.json'):
            self.filename = LabelFile.get_source(filename)
            self.labelFilename = filename
        else:
            self.filename = filename
            self.labelFilename = '.'.join(self.filename.split('.')[:-1] + ['json'])
        self.pointcloud = PointCloud(self.filename, max_points=max_points, render=False)
        self.annotations = LabelFile(self.labelFilename)
        points = self.pointcloud.points[['x', 'y', 'z']].values
        self.min_point, self.max_point = points.min(axis=0), points.max(axis=0)
        self.thickness = thickness
        self.min_slice, self.max_slice = int(self.min_point[2] / thickness) - 1, int(self.max_point[2] / thickness)
        self.slices = defaultdict(list)
        for idx in range(self.min_slice, self.max_slice + 1):
            vals = self.pointcloud.points['z']
            keep = np.where(np.logical_and(idx * thickness <= vals, vals < (idx + 1) * thickness))
            self.slices[idx] = self.pointcloud.points.index[keep].tolist()

    def rotate(self, angle, center=None, indices=None):
        if center is None:
            center = self.center
        self.rotate_annotations(angle)
        self.rotate_points(angle, indices)

    def rotate_annotations(self, angle):
        pass

    def rotate_points(self, angle, indices=None):
        angle = np.radians(angle)
        c, s = np.cos(angle), np.sin(angle)
        rot = np.array(((c, s), (-s, c)))
        if indices is None:
            self.pointcloud.points[['x', 'y']] = np.dot(self.pointcloud.points[['x', 'y']].values, rot)
        else:
            self.pointcloud.points.loc[indices][['x', 'y']] = np.dot(self.pointcloud.points.loc[indices][['x', 'y']].values, rot)

    def slice(self, idx):
        return self.pointcloud.points.loc[self.slices[idx]][['x', 'y', 'z']].values

    def save(self, pointFile, labelFile, shapes, otherData=None):
        self.pointcloud.write(pointFile, overwrite=True)
        self.annotations.save(labelFile, shapes, pointFile, otherData)

    @property
    def points(self):
        return self.pointcloud.points[['x', 'y', 'z']].values

    @property
    def center(self):
        return self.points.mean(axis=0)
