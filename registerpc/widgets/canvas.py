from qtpy import QtCore
from qtpy import QtGui
from qtpy import QtWidgets

from registerpc import QT5
from registerpc.shape import Shape
import registerpc.utils
import numpy as np
import qimage2ndarray


# TODO(unknown):
# - [maybe] Find optimal epsilon value.


CURSOR_DEFAULT = QtCore.Qt.ArrowCursor
CURSOR_POINT = QtCore.Qt.PointingHandCursor
CURSOR_DRAW = QtCore.Qt.CrossCursor
CURSOR_MOVE = QtCore.Qt.ClosedHandCursor
CURSOR_GRAB = QtCore.Qt.OpenHandCursor
CURSOR_ROTATE = QtCore.Qt.BusyCursor


class Canvas(QtWidgets.QWidget):

    zoomRequest = QtCore.Signal(int, QtCore.QPoint)
    scrollRequest = QtCore.Signal(int, int)
    nextSliceRequest = QtCore.Signal()
    lastSliceRequest = QtCore.Signal()
    newShape = QtCore.Signal()
    breakRack = QtCore.Signal(QtCore.QPointF)
    selectionChanged = QtCore.Signal(list)
    shapeMoved = QtCore.Signal()
    drawingPolygon = QtCore.Signal(bool)
    edgeSelected = QtCore.Signal(bool, object)
    vertexSelected = QtCore.Signal(bool)
    rackChanged = QtCore.Signal(Shape)
    beamChanged = QtCore.Signal(Shape)
    rotateRack = QtCore.Signal()
    rotatePixmap = QtCore.Signal(float)
    translatePixmap = QtCore.Signal(float, float)
    roomRotated = QtCore.Signal(int, float, float, float)
    roomTranslated = QtCore.Signal(int, float, float)

    CREATE, EDIT = 0, 1

    # polygon, rectangle, line, or point
    _createMode = 'polygon'

    _fill_drawing = False

    def __init__(self, *args, **kwargs):
        self.epsilon = kwargs.pop('epsilon', 10.0)
        self.double_click = kwargs.pop('double_click', 'close')
        if self.double_click not in [None, 'close']:
            raise ValueError(
                'Unexpected value for double_click event: {}'
                .format(self.double_click)
            )
        super(Canvas, self).__init__(*args, **kwargs)
        # Initialise local state.
        self.mode = self.EDIT
        self.shapes = []
        self.shapesBackups = []
        self.current = None
        self.selectedShapes = []  # save the selected shapes here
        self.selectedShapesCopy = []
        # self.line represents:
        #   - createMode == 'polygon': edge from last point to current
        #   - createMode == 'rectangle': diagonal line of the rectangle
        #   - createMode == 'line': the line
        #   - createMode == 'point': the point

        self.line = Shape()
        self.prevPoint = QtCore.QPoint()
        self.prevMovePoint = QtCore.QPoint()
        #self.offsets = QtCore.QPoint(), QtCore.QPoint()
        self.scale = 1.0
        self.globalBounds = QtGui.QPixmap()
        self.images = []
        self.offsets = []
        self.rotations = []
        self.translations = []
        self.imageBounds = []
        self.locked = None
        self.roomIdx = None
        self.delta_theta = 1.0
        self.delta = 4
        self.rotating = False
        self.translating = False
        self.visible = {}
        self._hideBackround = False
        self.hideBackround = False
        self.hShape = None
        self.hVertex = None
        self.hEdge = None
        self.movingShape = False
        self.translating = False
        self.rotating = False
        self._painter = QtGui.QPainter()
        self._cursor = CURSOR_DEFAULT
        # Menus:
        # 0: right-click without selection and dragging of shapes
        # 1: right-click with selection and dragging of shapes
        self.menus = (QtWidgets.QMenu(), QtWidgets.QMenu())
        # Set widget options.
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.WheelFocus)

    def fillDrawing(self):
        return self._fill_drawing

    def setFillDrawing(self, value):
        self._fill_drawing = value

    @property
    def createMode(self):
        return self._createMode

    @createMode.setter
    def createMode(self, value):
        if value not in ['polygon', 'rectangle', 'circle', 'line', 'point', 'linestrip']:
            raise ValueError('Unsupported createMode: %s' % value)
        self._createMode = value

    def storeShapes(self):
        shapesBackup = []
        for shape in self.shapes:
            shapesBackup.append(shape.copy())
        if len(self.shapesBackups) >= 10:
            self.shapesBackups = self.shapesBackups[-9:]
        self.shapesBackups.append(shapesBackup)

    @property
    def isShapeRestorable(self):
        if len(self.shapesBackups) < 2:
            return False
        return True

    def restoreShape(self):
        if not self.isShapeRestorable:
            return
        self.shapesBackups.pop()  # latest
        shapesBackup = self.shapesBackups.pop()
        self.shapes = shapesBackup
        self.selectedShapes = []
        for shape in self.shapes:
            shape.selected = False
        self.repaint()

    def enterEvent(self, ev):
        self.overrideCursor(self._cursor)

    def leaveEvent(self, ev):
        self.unHighlight()
        self.restoreCursor()

    def focusOutEvent(self, ev):
        self.restoreCursor()

    def isVisible(self, shape):
        return self.visible.get(shape, True)

    def drawing(self):
        return self.mode == self.CREATE

    def editing(self):
        return self.mode == self.EDIT

    def setEditing(self, value=True):
        self.mode = self.EDIT if value else self.CREATE
        if not value:  # Create
            self.unHighlight()
            self.deSelectShape()

    def unHighlight(self):
        if self.hShape:
            self.hShape.highlightClear()
            self.update()

    def selectedVertex(self):
        return self.hVertex is not None

    def mouseMoveEvent(self, ev):
        """Update line with last point and current coordinates."""
        try:
            if QT5:
                pos = self.transformPos(ev.localPos())
            else:
                pos = self.transformPos(ev.posF())
        except AttributeError:
            return

        self.prevMovePoint = pos
        self.restoreCursor()

        # Rotating.
        if QtCore.Qt.RightButton & ev.buttons():
            if self.prevPoint and self.roomIdx is not None and not self.locked[self.roomIdx]:
                self.overrideCursor(CURSOR_ROTATE)
                center = self.getSelectedRoomCenter()
                angle = self.getAngleFromPosition(pos, center)
                if angle:
                    self.rotations[self.roomIdx] += np.degrees(angle)
                    self.repaint()
            return

        # Translating
        if QtCore.Qt.LeftButton & ev.buttons():
            if self.prevPoint and self.roomIdx is not None and not self.locked[self.roomIdx]:
                self.overrideCursor(CURSOR_MOVE)
                dp = self.getDisplacementFromPosition(pos)
                if dp:
                    self.translations[self.roomIdx] += dp
                    self.repaint()
            return

        # Just hovering over the canvas
        room = self.getRoomFromPosition(pos)
        if room is None or not len(self.locked):
            return
        if self.locked[room]:
            self.setToolTip(self.tr('Room locked'))
        else:
            self.setToolTip(self.tr('Room unlocked'))

    def addPointToEdge(self):
        shape = self.hShape
        index = self.hEdge
        point = self.prevMovePoint
        if shape is None or index is None or point is None:
            return
        shape.insertPoint(index, point)
        shape.highlightVertex(index, shape.MOVE_VERTEX)
        self.hShape = shape
        self.hVertex = index
        self.hEdge = None
        self.movingShape = True

    def removeSelectedPoint(self):
        if (self.hShape is None and
                self.prevMovePoint is None):
            return
        shape = self.hShape
        point = self.prevMovePoint
        index = shape.nearestVertex(point, self.epsilon)
        shape.removePoint(index)
        # shape.highlightVertex(index, shape.MOVE_VERTEX)
        self.hShape = shape
        self.hVertex = None
        self.hEdge = None
        self.movingShape = True  # Save changes

    def getEdges(self, shape):
        x, y = shape.points[0].x(), shape.points[0].y()
        w, h = self.globalBounds.width(), self.globalBounds.height()
        x1 = QtCore.QPoint(0, y)
        x2 = QtCore.QPoint(w, y)
        y1 = QtCore.QPoint(x, 0)
        y2 = QtCore.QPoint(x, h)
        return [QtCore.QLine(x1, x2), QtCore.QLine(y1, y2)]

    def mousePressEvent(self, ev):
        if QT5:
            pos = self.transformPos(ev.localPos())
        else:
            pos = self.transformPos(ev.posF())
        if ev.button() == QtCore.Qt.LeftButton:
            # If the room has been rotated, push the rotation to the pointcloud
            self.roomIdx = self.getRoomFromPosition(pos)
            if self.roomIdx and self.rotations[self.roomIdx]:
                center = self.getSelectedRoomCenter(points_frame=True)
                self.roomRotated.emit(self.roomIdx, self.rotations[self.roomIdx], center.x(), center.y())
                self.rotations[self.roomIdx] = 0.0
            self.rotating, self.translating = False, True
            self.prevPoint = pos
            return
        elif ev.button() == QtCore.Qt.RightButton:
            self.roomIdx = self.getRoomFromPosition(pos)
            self.rotating, self.translating = True, False
            self.prevPoint = pos
            return

    def mouseReleaseEvent(self, ev):
        if QT5:
            pos = self.transformPos(ev.localPos())
        else:
            pos = self.transformPos(ev.posF())

        if ev.button() == QtCore.Qt.LeftButton:
            if self.roomIdx is not None and not self.translations[self.roomIdx].isNull():
                trans = self.translations[self.roomIdx]
                self.roomTranslated.emit(self.roomIdx, trans.x(), -trans.y())
                self.translations[self.roomIdx] = QtCore.QPointF(0.0, 0.0)

    def endMove(self, copy):
        assert self.selectedShapes and self.selectedShapesCopy
        assert len(self.selectedShapesCopy) == len(self.selectedShapes)
        if copy:
            for i, shape in enumerate(self.selectedShapesCopy):
                self.shapes.append(shape)
                self.selectedShapes[i].selected = False
                self.selectedShapes[i] = shape
        else:
            for i, shape in enumerate(self.selectedShapesCopy):
                self.selectedShapes[i].points = shape.points
        self.selectedShapesCopy = []
        self.repaint()
        self.storeShapes()
        return True

    def hideBackroundShapes(self, value):
        self.hideBackround = value
        if self.selectedShapes:
            # Only hide other shapes if there is a current selection.
            # Otherwise the user will not be able to select a shape.
            self.setHiding(True)
            self.repaint()

    def setHiding(self, enable=True):
        self._hideBackround = self.hideBackround if enable else False

    def canCloseShape(self):
        return self.drawing() and self.current and len(self.current) > 2

    def mouseDoubleClickEvent(self, ev):
        if QT5:
            pos = self.transformPos(ev.localPos())
        else:
            pos = self.transformPos(ev.posF())
        self.roomIdx = self.getRoomFromPosition(pos)
        if self.roomIdx is not None:
            self.locked[self.roomIdx] = not self.locked[self.roomIdx]

    def selectShapes(self, shapes):
        self.setHiding()
        self.selectionChanged.emit(shapes)
        self.update()

    def selectShapePoint(self, point, multiple_selection_mode):
        """Select the first shape created which contains this point."""
        if self.selectedVertex():  # A vertex is marked for selection.
            index, shape = self.hVertex, self.hShape
            shape.highlightVertex(index, shape.MOVE_VERTEX)
        else:
            for shape in reversed(self.shapes):
                if self.isVisible(shape) and shape.containsPoint(point):
                    self.calculateOffsets(shape, point)
                    self.setHiding()
                    if multiple_selection_mode:
                        if shape not in self.selectedShapes:
                            self.selectionChanged.emit(self.selectedShapes + [shape])
                    else:
                        self.selectionChanged.emit([shape])
                    return
        self.deSelectShape()

    def calculateOffsets(self, shape, point):
        rect = shape.boundingRect()
        x1 = rect.x() - point.x()
        y1 = rect.y() - point.y()
        x2 = (rect.x() + rect.width() - 1) - point.x()
        y2 = (rect.y() + rect.height() - 1) - point.y()
        #self.offsets = QtCore.QPoint(x1, y1), QtCore.QPoint(x2, y2)

    def boundedMoveVertexRect(self, pos):
        # Move a vertex in a rectangle which includes moving 1 or two other vertices as well
        # Todo: move rect vertex
        pass

    def boundedMoveVertex(self, pos):
        if 'rack' in self.hShape.label:
            self.boundedMoveVertexRect(pos)
        else:
            index, shape = self.hVertex, self.hShape
            point = shape[index]
            if self.outOfPixmap(pos):
                pos = self.intersectionPoint(point, pos)
            shape.moveVertexBy(index, pos - point)

    def getSelectedRoomCenter(self, points_frame=False):
        offset = self.offsets[self.roomIdx]
        image = self.images[self.roomIdx]
        dims = QtCore.QPointF(image.width(), image.height())
        center = offset + dims / 2.0
        if not points_frame:
            center.setY(self.globalBounds.height() - center.y())
        return center

    def getAngleFromPosition(self, pos, center):
        a, b = pos - center, self.prevPoint - center
        theta_a, theta_b = np.arctan2(a.y(), a.x()), np.arctan2(b.y(), b.x())
        self.prevPoint = pos
        return theta_b - theta_a

    def getDisplacementFromPosition(self, pos):
        dp = pos - self.prevPoint
        self.prevPoint = pos
        return dp

    def boundedMoveShapes(self, shapes, pos):
        # XXX: The next line tracks the new position of the cursor
        # relative to the shape, but also results in making it
        # a bit "shaky" when nearing the border and allows it to
        # go outside of the shape's area for some reason.
        # self.calculateOffsets(self.selectedShapes, pos)
        dp = pos - self.prevPoint
        if dp:
            for shape in shapes:
                shape.moveBy(dp)
                if 'rack' in shape.label:
                    shape.calculateRackExitEdge()
            self.prevPoint = pos
            return True
        return False

    def deSelectShape(self):
        if self.selectedShapes:
            self.setHiding(False)
            self.selectionChanged.emit([])
            self.update()

    def deleteSelected(self):
        deleted_shapes = []
        if self.selectedShapes:
            for shape in self.selectedShapes:
                self.shapes.remove(shape)
                deleted_shapes.append(shape)
            self.storeShapes()
            self.selectedShapes = []
            self.update()
        return deleted_shapes

    def copySelectedShapes(self):
        if self.selectedShapes:
            self.selectedShapesCopy = [s.copy() for s in self.selectedShapes]
            self.boundedShiftShapes(self.selectedShapesCopy)
            self.endMove(copy=True)
        return self.selectedShapes

    def boundedShiftShapes(self, shapes):
        # Try to move in one direction, and if it fails in another.
        # Give up if both fail.
        point = shapes[0][0]
        offset = QtCore.QPoint(2.0, 2.0)
        #self.offsets = QtCore.QPoint(), QtCore.QPoint()
        self.prevPoint = point
        if not self.boundedMoveShapes(shapes, point - offset):
            self.boundedMoveShapes(shapes, point + offset)

    def getRotatedImageAndOffset(self, idx):
        image = self.images[idx]
        dx, dy = 0.0, 0.0
        if self.rotations[idx]:
            rotation = QtGui.QTransform()
            rotation.rotate(-self.rotations[idx])
            dx, dy = image.width(), image.height()
            image = image.transformed(rotation)
            dx, dy = (dx - image.width()) / 2., (dy - image.height()) / 2.
        dx, dy = self.offsets[idx].x() + dx, self.globalBounds.height() - self.offsets[idx].y() - image.height() - dy
        return image, QtCore.QPointF(dx, dy)

    def paintEvent(self, event):
        if not self.images:
            return super(Canvas, self).paintEvent(event)

        p = self._painter
        p.begin(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.HighQualityAntialiasing)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)

        p.scale(self.scale, self.scale)
        p.translate(self.offsetToCenter())

        for i in range(len(self.images)):
            image, offset = self.getRotatedImageAndOffset(i)
            offset += self.translations[i]
            p.drawPixmap(offset.x(), offset.y(), QtGui.QPixmap.fromImage(image))

        Shape.scale = self.scale
        for shape in self.shapes:
            if (shape.selected or not self._hideBackround) and \
                    self.isVisible(shape):
                shape.fill = shape.selected or shape == self.hShape
                shape.paint(p)
        if self.current:
            self.current.paint(p)
            self.line.paint(p)
        if self.selectedShapesCopy:
            for s in self.selectedShapesCopy:
                s.paint(p)

        if (self.fillDrawing() and self.createMode == 'polygon' and
                self.current is not None and len(self.current.points) >= 2):
            drawing_shape = self.current.copy()
            drawing_shape.addPoint(self.line[1])
            drawing_shape.fill = True
            drawing_shape.fill_color.setAlpha(64)
            drawing_shape.paint(p)

        p.end()

    def transformPos(self, point):
        """Convert from widget-logical coordinates to painter-logical ones."""
        return point / self.scale - self.offsetToCenter()

    def offsetToCenter(self):
        s = self.scale
        area = super(Canvas, self).size()
        w, h = self.globalBounds.width() * s, self.globalBounds.height() * s
        aw, ah = area.width(), area.height()
        x = (aw - w) / (2 * s) if aw > w else 0
        y = (ah - h) / (2 * s) if ah > h else 0
        return QtCore.QPoint(x, y)

    def outOfPixmap(self, p):
        w, h = self.globalBounds.width(), self.globalBounds.height()
        return not (0 <= p.x() <= w - 1 and 0 <= p.y() <= h - 1)

    def finalise(self):
        assert self.current
        if self.current.shape_type == 'rectangle':
            min_p, max_p = QtCore.QPointF(), QtCore.QPointF()
            min_p.setX(min(self.current.points[0].x(), self.current.points[1].x()))
            min_p.setY(min(self.current.points[0].y(), self.current.points[1].y()))
            max_p.setX(max(self.current.points[0].x(), self.current.points[1].x()))
            max_p.setY(max(self.current.points[0].y(), self.current.points[1].y()))
            self.current.points[0], self.current.points[1] = min_p, max_p
        self.current.close()
        self.shapes.append(self.current)
        self.storeShapes()
        self.current = None
        self.setHiding(False)
        self.newShape.emit()
        self.update()

    def closeEnough(self, p1, p2):
        # d = distance(p1 - p2)
        # m = (p1-p2).manhattanLength()
        # print "d %.2f, m %d, %.2f" % (d, m, d - m)
        # divide by scale to allow more precision when zoomed in
        return registerpc.utils.distance(p1 - p2) < (self.epsilon / self.scale)

    def intersectionPoint(self, p1, p2):
        # Cycle through each image edge in clockwise fashion,
        # and find the one intersecting the current line segment.
        # http://paulbourke.net/geometry/lineline2d/
        size = self.globalBounds.size()
        points = [(0, 0),
                  (size.width() - 1, 0),
                  (size.width() - 1, size.height() - 1),
                  (0, size.height() - 1)]
        # x1, y1 should be in the pixmap, x2, y2 should be out of the pixmap
        x1 = min(max(p1.x(), 0), size.width() - 1)
        y1 = min(max(p1.y(), 0), size.height() - 1)
        x2, y2 = p2.x(), p2.y()
        d, i, (x, y) = min(self.intersectingEdges((x1, y1), (x2, y2), points))
        x3, y3 = points[i]
        x4, y4 = points[(i + 1) % 4]
        if (x, y) == (x1, y1):
            # Handle cases where previous point is on one of the edges.
            if x3 == x4:
                return QtCore.QPoint(x3, min(max(0, y2), max(y3, y4)))
            else:  # y3 == y4
                return QtCore.QPoint(min(max(0, x2), max(x3, x4)), y3)
        return QtCore.QPoint(x, y)

    def intersectingEdges(self, point1, point2, points):
        """Find intersecting edges.

        For each edge formed by `points', yield the intersection
        with the line segment `(x1,y1) - (x2,y2)`, if it exists.
        Also return the distance of `(x2,y2)' to the middle of the
        edge along with its index, so that the one closest can be chosen.
        """
        (x1, y1) = point1
        (x2, y2) = point2
        for i in range(4):
            x3, y3 = points[i]
            x4, y4 = points[(i + 1) % 4]
            denom = (y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1)
            nua = (x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)
            nub = (x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)
            if denom == 0:
                # This covers two cases:
                #   nua == nub == 0: Coincident
                #   otherwise: Parallel
                continue
            ua, ub = nua / denom, nub / denom
            if 0 <= ua <= 1 and 0 <= ub <= 1:
                x = x1 + ua * (x2 - x1)
                y = y1 + ua * (y2 - y1)
                m = QtCore.QPoint((x3 + x4) / 2, (y3 + y4) / 2)
                d = registerpc.utils.distance(m - QtCore.QPoint(x2, y2))
                yield d, i, (x, y)

    # These two, along with a call to adjustSize are required for the
    # scroll area.
    def sizeHint(self):
        return self.minimumSizeHint()

    def minimumSizeHint(self):
        if self.globalBounds:
            return self.scale * self.globalBounds.size()
        return super(Canvas, self).minimumSizeHint()

    def wheelEvent(self, ev):
        if QT5:
            mods = ev.modifiers()
            delta = ev.angleDelta()
            if QtCore.Qt.ControlModifier == int(mods):
                # with Ctrl/Command key
                # zoom
                self.zoomRequest.emit(delta.y(), ev.pos())
            elif QtCore.Qt.ShiftModifier == int(mods):
                # with Shift key, scroll through slices
                if delta.y() > 0:
                    self.nextSliceRequest.emit()
                else:
                    self.lastSliceRequest.emit()
            else:
                # scroll
                self.scrollRequest.emit(delta.x(), QtCore.Qt.Horizontal)
                self.scrollRequest.emit(delta.y(), QtCore.Qt.Vertical)
        else:
            if ev.orientation() == QtCore.Qt.Vertical:
                mods = ev.modifiers()
                if QtCore.Qt.ControlModifier == int(mods):
                    # with Ctrl/Command key
                    self.zoomRequest.emit(ev.delta(), ev.pos())
                else:
                    self.scrollRequest.emit(
                        ev.delta(),
                        QtCore.Qt.Horizontal
                        if (QtCore.Qt.ShiftModifier == int(mods))
                        else QtCore.Qt.Vertical)
            else:
                self.scrollRequest.emit(ev.delta(), QtCore.Qt.Horizontal)
        ev.accept()

    def keyPressEvent(self, ev):
        if self.roomIdx is None or self.locked[self.roomIdx]:
            return
        if self.rotating:
            if ev.key() == QtCore.Qt.Key_Right:
                self.rotations[self.roomIdx] -= self.delta_theta
                self.repaint()
            elif ev.key() == QtCore.Qt.Key_Left:
                self.rotations[self.roomIdx] += self.delta_theta
                self.repaint()
            elif ev.key() == QtCore.Qt.Key_Up or ev.key() == QtCore.Qt.Key_Equal or ev.key() == QtCore.Qt.Key_Plus:
                self.delta_theta = min(16.0, self.delta_theta * 2.)
            elif ev.key() == QtCore.Qt.Key_Down or ev.key() == QtCore.Qt.Key_Minus:
                self.delta_theta = max(0.05, self.delta_theta / 2.)
            elif ev.key() == QtCore.Qt.Key_Enter and self.roomIdx is not None and self.rotations[self.roomIdx]:
                center = self.getSelectedRoomCenter(points_frame=True)
                self.roomRotated.emit(self.roomIdx, self.rotations[self.roomIdx], center.x(), center.y())
                self.rotations[self.roomIdx] = 0.0
        elif self.translating:
            # If the room has been rotated, push the rotation to the pointcloud
            if self.rotations[self.roomIdx]:
                center = self.getSelectedRoomCenter(points_frame=True)
                self.roomRotated.emit(self.roomIdx, self.rotations[self.roomIdx], center.x(), center.y())
                self.rotations[self.roomIdx] = 0.0
            if ev.key() == QtCore.Qt.Key_Right:
                self.roomTranslated.emit(self.roomIdx, self.delta, 0.)
            elif ev.key() == QtCore.Qt.Key_Left:
                self.roomTranslated.emit(self.roomIdx, -self.delta, 0.)
            elif ev.key() == QtCore.Qt.Key_Up:
                self.roomTranslated.emit(self.roomIdx, 0., self.delta)
            elif ev.key() == QtCore.Qt.Key_Down:
                self.roomTranslated.emit(self.roomIdx, 0., -self.delta)
            elif ev.key() == QtCore.Qt.Key_Equal or ev.key() == QtCore.Qt.Key_Plus:
                self.delta = min(128, self.delta * 2)
            elif ev.key() == ev.key() == QtCore.Qt.Key_Minus:
                self.delta = min(1, self.delta / 2)

    def setLastLabel(self, text, flags):
        assert text
        self.shapes[-1].label = text
        self.shapes[-1].flags = flags
        self.shapesBackups.pop()
        self.storeShapes()
        return self.shapes[-1]

    def undoLastLine(self):
        assert self.shapes
        self.current = self.shapes.pop()
        self.current.setOpen()
        if self.createMode in ['polygon', 'linestrip']:
            self.line.points = [self.current[-1], self.current[0]]
        elif self.createMode in ['rectangle', 'line', 'circle']:
            self.current.points = self.current.points[0:1]
        elif self.createMode == 'point':
            self.current = None
        self.drawingPolygon.emit(True)

    def undoLastPoint(self):
        if not self.current or self.current.isClosed():
            return
        self.current.popPoint()
        if len(self.current) > 0:
            self.line[0] = self.current[-1]
        else:
            self.current = None
            self.drawingPolygon.emit(False)
        self.repaint()

    def calculateGlobalBounds(self):
        min_x, min_y, max_x, max_y = np.inf, np.inf, -np.inf, -np.inf
        for image, offset in zip(self.images, self.offsets):
            min_x, min_y = min(min_x, offset.x()), min(min_y, offset.y())
            max_x, max_y = max(max_x, offset.x() + image.width()), max(max_y, offset.y() + image.height())
        self.globalBounds = QtGui.QPixmap(max_x - min_x, max_y - min_y)

    def getCanvasOffset(self, idx):
        x, y = self.offsets[idx].x(), self.globalBounds.height() - self.offsets[idx].y() - self.images[idx].height()
        return QtCore.QPointF(x, y)

    def getImageBounds(self, idx):
        offset = self.getCanvasOffset(idx)
        return [offset, QtCore.QPointF(offset.x() + self.images[idx].width(), offset.y() + self.images[idx].height())]

    def loadImage(self, idx, image, offset):
        self.images[idx] = image
        self.offsets[idx] = QtCore.QPointF(offset[0], offset[1])
        self.rotations[idx] = 0.0
        self.translations[idx] = QtCore.QPointF(0.0, 0.0)
        self.calculateGlobalBounds()
        self.imageBounds[idx] = self.getImageBounds(idx)
        self.shapes = []
        self.repaint()

    def loadImages(self, images, offsets):
        self.images = images
        self.offsets = [QtCore.QPointF(off[0], off[1]) for off in offsets]
        self.rotations = [0.0] * len(self.images)
        self.translations = [QtCore.QPointF(0.0, 0.0) for _ in range(len(self.images))]
        self.calculateGlobalBounds()
        self.imageBounds = [self.getImageBounds(idx) for idx in range(len(self.images))]
        if self.locked is None:
            self.locked = [True] * len(self.images)
        self.shapes = []
        self.repaint()

    def loadShapes(self, shapes, replace=True, store=True):
        if replace:
            self.shapes = list(shapes)
        else:
            self.shapes.extend(shapes)
        if store:
            self.storeShapes()
        self.current = None
        self.hShape = None
        self.hVertex = None
        self.hEdge = None
        self.repaint()

    def setShapeVisible(self, shape):
        self.visible[shape] = True
        self.repaint()

    def setShapeInvisible(self, shape):
        self.visible[shape] = False
        self.repaint()

    def getVisible(self, shape):
        return self.visible[shape]

    def overrideCursor(self, cursor):
        self.restoreCursor()
        self._cursor = cursor
        QtWidgets.QApplication.setOverrideCursor(cursor)

    def restoreCursor(self):
        QtWidgets.QApplication.restoreOverrideCursor()

    def resetState(self):
        self.restoreCursor()
        self.images = []
        self.offsets = []
        self.rotations = []
        self.translations = []
        self.imageBounds = []
        self.locked = []
        self.delta_theta = 1.0
        self.delta = 4
        self.globalBounds = QtGui.QPixmap()
        self.shapesBackups = []
        self.update()

    def getRoomFromPosition(self, pos):
        best_room, smallest_area = None, np.inf
        for idx, box in enumerate(self.imageBounds):
            cur_pos = pos + self.translations[idx]
            if box[0].x() < cur_pos.x() < box[1].x() and box[0].y() < cur_pos.y() < box[1].y():
                area = (box[1].x() - box[0].x()) * (box[1].y() - box[0].y())
                if area < smallest_area:
                    smallest_area = area
                    best_room = idx
        return best_room

    def getRoomFromPix(self, pos):
        r = 5
        best_room, best_pixels = None, 0
        for idx, box in enumerate(self.imageBounds):
            # Check to see if clicked position is inside the bounds of current image
            if box[0].x() < pos.x() < box[1].x() and box[0].y() < pos.y() < box[1].y():
                # Check to see if the number of nearby pixels is greater than the current max
                values = qimage2ndarray.alpha_view(self.images[idx])
                x, y = int(pos.x() - box[0].x()), int(pos.y() - box[0].y())
                min_x, min_y = max(0, x-r), max(0, y-r)
                max_x, max_y = min(values.shape[0]-1, x+r+1), min(values.shape[1]-1, y+r+1)
                pixels = values[min_x:max_x, min_y:max_y].sum()
                if pixels > best_pixels:
                    best_pixels = pixels
                    best_room = idx
        return best_room
