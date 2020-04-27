# -*- coding: utf-8 -*-

import functools
import os
import os.path as osp
import re
import webbrowser
from tqdm import tqdm, trange

import PIL.Image
from io import BytesIO
import numpy as np

import imgviz
from qtpy import QtCore
from qtpy.QtCore import Qt
from qtpy import QtGui
from qtpy import QtWidgets
from PyQt5.QtWidgets import QInputDialog, QDialog, QLineEdit, QDialogButtonBox, QFormLayout, QProgressBar

from registerpc import __appname__
from registerpc import PY2
from registerpc import QT5

from registerpc.dialogs.open_file_dialog import OpenFileDialog
from registerpc.dialogs.choose_file_dialog import ChooseFileDialog

from . import utils
from registerpc.config import get_config
from registerpc.label_file import LabelFile
from registerpc.label_file import LabelFileError
from registerpc.logger import logger
from registerpc.shape import Shape
from registerpc.widgets import Canvas
from registerpc.widgets import ColorDialog
from registerpc.widgets import LabelDialog
from registerpc.widgets import LabelQListWidget
from registerpc.widgets import ToolBar
from registerpc.widgets import UniqueLabelQListWidget
from registerpc.widgets import ZoomWidget

from registerpc.Room import Room
from registerpc.pointcloud.PointCloud import PointCloud


# TODO:
#   --- BYU students:
#   --- Brady:
#   --- Austin:

LABEL_COLORMAP = imgviz.label_colormap(value=200)


class MainWindow(QtWidgets.QMainWindow):

    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = 0, 1, 2

    def __init__(
        self,
        config=None,
        filename=None,
        output=None,
        output_file=None,
        output_dir=None,
    ):

        if output is not None:
            logger.warning(
                'argument output is deprecated, use output_file instead'
            )
            if output_file is None:
                output_file = output

        # see registerpc/config/default_config.yaml for valid configuration
        if config is None:
            config = get_config()
        self._config = config

        super(MainWindow, self).__init__()
        self.setWindowTitle(__appname__)

        # Whether we need to save or not.
        self.dirty = False

        self._noSelectionSlot = False

        # Main widgets and related state.
        self.labelDialog = LabelDialog(
            parent=self,
            labels=self._config['labels'],
            sort_labels=self._config['sort_labels'],
            show_text_field=self._config['show_label_text_field'],
            completion=self._config['label_completion'],
            fit_to_content=self._config['fit_to_content'],
            flags=self._config['label_flags']
        )

        self.lastOpenDir = None

        self.flag_dock = self.flag_widget = None
        self.flag_dock = QtWidgets.QDockWidget(self.tr('Flags'), self)
        self.flag_dock.setObjectName('Flags')
        self.flag_widget = QtWidgets.QListWidget()
        if config['flags']:
            self.loadFlags({k: False for k in config['flags']})
        self.flag_dock.setWidget(self.flag_widget)
        self.flag_widget.itemChanged.connect(self.setDirty)

        self.labelList = LabelQListWidget()
        # Connect to itemChanged to detect checkbox changes.
        self.labelList.setDragDropMode(
            QtWidgets.QAbstractItemView.InternalMove)
        self.labelList.setParent(self)
        self.shape_dock = QtWidgets.QDockWidget(
            self.tr('Polygon Labels'),
            self
        )
        self.shape_dock.setObjectName('Labels')
        self.shape_dock.setWidget(self.labelList)

        self.uniqLabelList = UniqueLabelQListWidget()
        self.uniqLabelList.setToolTip(self.tr(
            "Select label to start annotating for it. "
            "Press 'Esc' to deselect."))
        if self._config['labels']:
            for label in self._config['labels']:
                item = self.uniqLabelList.createItemFromLabel(label)
                self.uniqLabelList.addItem(item)
                rgb = self._get_rgb_by_label(label)
                self.uniqLabelList.setItemLabel(item, label, rgb)
        self.label_dock = QtWidgets.QDockWidget(self.tr(u'Label List'), self)
        self.label_dock.setObjectName(u'Label List')
        self.label_dock.setWidget(self.uniqLabelList)

        self.fileSearch = QtWidgets.QLineEdit()
        self.fileSearch.setPlaceholderText(self.tr('Search Filename'))
        self.fileListWidget = QtWidgets.QListWidget()
        #self.fileListWidget.itemSelectionChanged.connect(self.fileSelectionChanged)
        fileListLayout = QtWidgets.QVBoxLayout()
        fileListLayout.setContentsMargins(0, 0, 0, 0)
        fileListLayout.setSpacing(0)
        #fileListLayout.addWidget(self.fileSearch)
        fileListLayout.addWidget(self.fileListWidget)
        self.file_dock = QtWidgets.QDockWidget(self.tr(u'File List'), self)
        self.file_dock.setObjectName(u'Files')
        fileListWidget = QtWidgets.QWidget()
        fileListWidget.setLayout(fileListLayout)
        self.file_dock.setWidget(fileListWidget)

        self.zoomWidget = ZoomWidget()
        self.colorDialog = ColorDialog(parent=self)

        self.canvas = self.labelList.canvas = Canvas(
            epsilon=self._config['epsilon'],
            double_click=self._config['canvas']['double_click'],
        )
        self.canvas.zoomRequest.connect(self.zoomRequest)

        scrollArea = QtWidgets.QScrollArea()
        scrollArea.setWidget(self.canvas)
        scrollArea.setWidgetResizable(True)
        self.scrollBars = {
            Qt.Vertical: scrollArea.verticalScrollBar(),
            Qt.Horizontal: scrollArea.horizontalScrollBar(),
        }
        self.canvas.scrollRequest.connect(self.scrollRequest)
        self.canvas.nextSliceRequest.connect(self.showNextSlice)
        self.canvas.lastSliceRequest.connect(self.showLastSlice)

        self.canvas.roomRotated.connect(self.roomRotated)
        self.canvas.roomTranslated.connect(self.roomTranslated)

        self.setCentralWidget(scrollArea)

        features = QtWidgets.QDockWidget.DockWidgetFeatures()
        for dock in ['flag_dock', 'label_dock', 'shape_dock', 'file_dock']:
            if self._config[dock]['closable']:
                features = features | QtWidgets.QDockWidget.DockWidgetClosable
            if self._config[dock]['floatable']:
                features = features | QtWidgets.QDockWidget.DockWidgetFloatable
            if self._config[dock]['movable']:
                features = features | QtWidgets.QDockWidget.DockWidgetMovable
            getattr(self, dock).setFeatures(features)
            if self._config[dock]['show'] is False:
                getattr(self, dock).setInvisible(False)

        self.addDockWidget(Qt.RightDockWidgetArea, self.flag_dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.label_dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.shape_dock)
        self.addDockWidget(Qt.RightDockWidgetArea, self.file_dock)

        # Actions
        action = functools.partial(utils.newAction, self)
        shortcuts = self._config['shortcuts']
        quit = action(self.tr('&Quit'), self.close, shortcuts['quit'], 'quit',
                      self.tr('Quit application'))
        open_ = action(self.tr('&Open'),
                       self.openPointCloud,
                       shortcuts['open'],
                       'open',
                       self.tr('Open point cloud file'))
        showNextSlice = action(
            self.tr('Next Slice'),
            self.showNextSlice,
            shortcuts['open_next'],
            'next slice',
            self.tr(u'Show next slice of point cloud'),
            enabled=False,
        )

        showLastSlice = action(
            self.tr('Last Slice'),
            self.showLastSlice,
            shortcuts['open_prev'],
            'next slice',
            self.tr(u'Show previous slice of point cloud'),
            enabled=False,
        )

        save = action(self.tr('&Save'),
                      self.saveFiles, shortcuts['save'], 'save',
                      self.tr('Save changes to files'), enabled=False)
        saveAs = action(self.tr('&Save As'), self.saveFileAs,
                        shortcuts['save_as'],
                        'save-as', self.tr('Save changes to different files'),
                        enabled=False)

        saveAuto = action(
            text=self.tr('Save &Automatically'),
            slot=lambda x: self.actions.saveAuto.setChecked(x),
            icon='save',
            tip=self.tr('Save automatically'),
            checkable=True,
            enabled=True,
        )
        saveAuto.setChecked(self._config['auto_save'])

        close = action('&Close', self.closeFile, shortcuts['close'], 'close',
                       'Close current file')

        render_3d = action('Render points in 3D', self.render3d, None, 'render', 'Render the points in 3D')

        rotate = action(self.tr('Rotate'), self.setRotating, None, None, self.tr('Rotate the selected room'), enabled=False)

        translate = action(self.tr('Translate'), self.setTranslating, None, None, self.tr('Translate the selected room'),
                           enabled=False)

        hideAll = action(self.tr('&Hide\nPolygons'),
                         functools.partial(self.togglePolygons, False),
                         icon='eye', tip=self.tr('Hide all polygons'),
                         enabled=False)

        showAll = action(self.tr('&Show\nPolygons'),
                         functools.partial(self.togglePolygons, True),
                         icon='eye', tip=self.tr('Show all polygons'),
                         enabled=False)

        help = action(self.tr('&Tutorial'), self.tutorial, icon='help',
                      tip=self.tr('Show tutorial page'))

        zoom = QtWidgets.QWidgetAction(self)
        zoom.setDefaultWidget(self.zoomWidget)
        self.zoomWidget.setWhatsThis(
            self.tr(
                'Zoom in or out of the image. Also accessible with '
                '{} and {} from the canvas.'
            ).format(
                utils.fmtShortcut(
                    '{},{}'.format(
                        shortcuts['zoom_in'], shortcuts['zoom_out']
                    )
                ),
                utils.fmtShortcut(self.tr("Ctrl+Wheel")),
            )
        )
        self.zoomWidget.setEnabled(False)

        zoomIn = action(self.tr('Zoom &In'),
                        functools.partial(self.addZoom, 1.1),
                        shortcuts['zoom_in'], 'zoom-in',
                        self.tr('Increase zoom level'), enabled=False)
        zoomOut = action(self.tr('&Zoom Out'),
                         functools.partial(self.addZoom, 0.9),
                         shortcuts['zoom_out'], 'zoom-out',
                         self.tr('Decrease zoom level'), enabled=False)
        zoomOrg = action(self.tr('&Original size'),
                         functools.partial(self.setZoom, 100),
                         shortcuts['zoom_to_original'], 'zoom',
                         self.tr('Zoom to original size'), enabled=False)
        fitWindow = action(self.tr('&Fit Window'), self.setFitWindow,
                           shortcuts['fit_window'], 'fit-window',
                           self.tr('Zoom follows window size'), checkable=True,
                           enabled=False)
        fitWidth = action(self.tr('Fit &Width'), self.setFitWidth,
                          shortcuts['fit_width'], 'fit-width',
                          self.tr('Zoom follows window width'),
                          checkable=True, enabled=False)
        # Group zoom controls into a list for easier toggling.
        zoomActions = (self.zoomWidget, zoomIn, zoomOut, zoomOrg,
                       fitWindow, fitWidth)
        self.zoomMode = self.FIT_WINDOW
        fitWindow.setChecked(Qt.Checked)
        self.scalers = {
            self.FIT_WINDOW: self.scaleFitWindow,
            self.FIT_WIDTH: self.scaleFitWidth,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        # Label list context menu.
        labelMenu = QtWidgets.QMenu()
        self.labelList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.labelList.customContextMenuRequested.connect(
            self.popLabelListMenu)

        # File list context menu.
        fileMenu = QtWidgets.QMenu()
        utils.addActions(fileMenu, (rotate, translate))
        self.fileListWidget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.fileListWidget.customContextMenuRequested.connect(
            self.popFileListMenu)

        # Store actions for further handling.
        self.actions = utils.struct(
            saveAuto=saveAuto,
            save=save, saveAs=saveAs, open=open_, close=close,
            zoom=zoom, zoomIn=zoomIn, zoomOut=zoomOut, zoomOrg=zoomOrg,
            fitWindow=fitWindow, fitWidth=fitWidth,
            zoomActions=zoomActions,
            showNextSlice=showNextSlice, showLastSlice=showLastSlice,
            render3d=render_3d,
            fileMenuActions=(open_, save, saveAs, close, quit),
            tool=(),
            # XXX: need to add some actions here to activate the shortcut
            onLoadActive=(
                saveAs,
                close,
                showNextSlice,
                showLastSlice,
                render_3d,
                rotate,
                translate,
            ),
        )

        self.menus = utils.struct(
            file=self.menu(self.tr('&File')),
            #edit=self.menu(self.tr('&Edit')),
            view=self.menu(self.tr('&View')),
            help=self.menu(self.tr('&Help')),
            recentFiles=QtWidgets.QMenu(self.tr('Open &Recent')),
            labelList=labelMenu,
            fileListWidget=fileMenu,
        )

        utils.addActions(
            self.menus.file,
            (
                open_,
                showNextSlice,
                showLastSlice,
                self.menus.recentFiles,
                save,
                saveAs,
                saveAuto,
                close,
                None,
                quit,
            ),
        )

        utils.addActions(self.menus.help, (help,))
        utils.addActions(
            self.menus.view,
            (
                self.flag_dock.toggleViewAction(),
                self.label_dock.toggleViewAction(),
                self.shape_dock.toggleViewAction(),
                self.file_dock.toggleViewAction(),
                None,
                render_3d,
                None,
                zoomIn,
                zoomOut,
                zoomOrg,
                None,
                fitWindow,
                fitWidth,
                None,
            ),
        )

        self.menus.file.aboutToShow.connect(self.updateFileMenu)

        # Custom context menu for the canvas widget:
        #utils.addActions(self.canvas.menus[0], self.actions.menu)

        self.tools = self.toolbar('Tools')
        # Menu buttons on Left
        self.actions.tool = (
            open_,
            showNextSlice,
            showLastSlice,
            save,
            None,
            zoomIn,
            zoom,
            zoomOut,
            fitWindow,
            fitWidth,
            render_3d,
        )

        self.statusBar().showMessage(self.tr('%s started.') % __appname__)
        self.statusBar().show()
        self.progressBar = QProgressBar()

        self.statusBar().addPermanentWidget(self.progressBar)

        # This is simply to show the bar
        self.progressBar.setGeometry(30, 40, 200, 25)
        self.progressBar.setMaximum(100)

        if output_file is not None and self._config['auto_save']:
            logger.warn(
                'If `auto_save` argument is True, `output_file` argument '
                'is ignored and output filename is automatically '
                'set as IMAGE_BASENAME.json.'
            )
        self.output_file = output_file
        self.output_dir = output_dir

        # Application state.
        self.max_points = None
        self.mesh = None
        self.thickness = None
        self.sliceIdx = 0
        self.rooms = []
        self.pointcloud3d = PointCloud(render=False)

        self.recentFiles = []
        self.maxRecent = 7
        self.otherData = {}
        self.zoom_level = 100
        self.fit_window = False
        self.zoom_values = {}  # key=filename, value=(zoom_mode, zoom_value)
        self.scroll_values = {
            Qt.Horizontal: {},
            Qt.Vertical: {},
        }  # key=filename, value=scroll_value

        if config['file_search']:
            self.fileSearch.setText(config['file_search'])

        # XXX: Could be completely declarative.
        # Restore application settings.
        self.settings = QtCore.QSettings('registerpc', 'registerpc')
        # FIXME: QSettings.value can return None on PyQt4
        self.recentFiles = self.settings.value('recentFiles', []) or []
        size = self.settings.value('window/size', QtCore.QSize(600, 500))
        position = self.settings.value('window/position', QtCore.QPoint(0, 0))
        self.resize(size)
        self.move(position)
        # or simply:
        # self.restoreGeometry(settings['window/geometry']
        self.restoreState(
            self.settings.value('window/state', QtCore.QByteArray()))

        # Populate the File menu dynamically.
        self.updateFileMenu()
        # Since loading the file may take some time,
        # make sure it runs in the background.
        '''
        if self.filename is not None:
            self.queueEvent(functools.partial(self.loadFiles, self.filename))
        '''

        # Callbacks:
        self.zoomWidget.valueChanged.connect(self.paintCanvas)

        self.populateModeActions()

        # self.firstStart = True
        # if self.firstStart:
        #    QWhatsThis.enterWhatsThisMode()

    def menu(self, title, actions=None):
        menu = self.menuBar().addMenu(title)
        if actions:
            utils.addActions(menu, actions)
        return menu

    def toolbar(self, title, actions=None):
        toolbar = ToolBar(title)
        toolbar.setObjectName('%sToolBar' % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        if actions:
            utils.addActions(toolbar, actions)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)
        return toolbar

    def setDirty(self):
        self.dirty = True
        title = __appname__
        self.setWindowTitle(title)
        self.actions.save.setEnabled(True)

    def setClean(self):
        self.dirty = False
        title = __appname__
        self.setWindowTitle(title)
        self.actions.save.setEnabled(False)

    def populateModeActions(self):
        tool = self.actions.tool
        self.tools.clear()
        utils.addActions(self.tools, tool)
        self.canvas.menus[0].clear()
        

    def toggleActions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for z in self.actions.zoomActions:
            z.setEnabled(value)
        for action in self.actions.onLoadActive:
            action.setEnabled(value)

    def queueEvent(self, function):
        QtCore.QTimer.singleShot(0, function)

    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)

    def resetState(self):
        self.rooms = []
        self.labelList.clear()
        self.fileListWidget.clear()
        self.sliceIdx = 0
        self.pointcloud3d.close_viewer()
        self.pointcloud3d = PointCloud(render=False)
        self.otherData = {}
        self.max_points = None
        self.thickness = None
        self.mesh = None
        self.canvas.resetState()

    def addRecentFile(self, filename):
        if filename in self.recentFiles:
            self.recentFiles.remove(filename)
        elif len(self.recentFiles) >= self.maxRecent:
            self.recentFiles.pop()
        self.recentFiles.insert(0, filename)

    # Callbacks

    def tutorial(self):
        url = 'https://github.com/wkentaro/labelme/tree/master/examples/tutorial'  # NOQA
        webbrowser.open(url)

    def updateFileMenu(self):
        def exists(filename):
            return osp.exists(str(filename))

        menu = self.menus.recentFiles
        menu.clear()
        files = [f for f in self.recentFiles if exists(f)]
        for i, f in enumerate(files):
            icon = utils.newIcon('labels')
            action = QtWidgets.QAction(
                icon, '&%d %s' % (i + 1, QtCore.QFileInfo(f).fileName()), self)
            action.triggered.connect(functools.partial(self.loadRecent, f))
            menu.addAction(action)

    def popLabelListMenu(self, point):
        self.menus.labelList.exec_(self.labelList.mapToGlobal(point))

    def popFileListMenu(self, point):
        self.menus.fileListWidget.exec_(self.fileListWidget.mapToGlobal(point))

    def _get_rgb_by_label(self, label):
        if self._config['shape_color'] == 'auto':
            item = self.uniqLabelList.findItemsByLabel(label)[0]
            label_id = self.uniqLabelList.indexFromItem(item).row() + 1
            label_id += self._config['shift_auto_shape_color']
            return LABEL_COLORMAP[label_id % len(LABEL_COLORMAP)]
        elif (self._config['shape_color'] == 'manual' and
              self._config['label_colors'] and
              label in self._config['label_colors']):
            return self._config['label_colors'][label]
        elif self._config['default_shape_color']:
            return self._config['default_shape_color']

    def scrollRequest(self, delta, orientation):
        units = - delta * 0.1  # natural scroll
        bar = self.scrollBars[orientation]
        value = bar.value() + bar.singleStep() * units
        self.setScroll(orientation, value)

    def setScroll(self, orientation, value):
        self.scrollBars[orientation].setValue(value)
        self.scroll_values[orientation][self.rooms[0].filename] = value

    def setZoom(self, value):
        self.actions.fitWidth.setChecked(False)
        self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.MANUAL_ZOOM
        self.zoomWidget.setValue(value)
        self.zoom_values[self.rooms[0].filename] = (self.zoomMode, value)

    def addZoom(self, increment=1.1):
        self.setZoom(self.zoomWidget.value() * increment)

    def zoomRequest(self, delta, pos):
        canvas_width_old = self.canvas.width()
        units = 1.1
        if delta < 0:
            units = 0.9
        self.addZoom(units)

        canvas_width_new = self.canvas.width()
        if canvas_width_old != canvas_width_new:
            canvas_scale_factor = canvas_width_new / canvas_width_old

            x_shift = round(pos.x() * canvas_scale_factor) - pos.x()
            y_shift = round(pos.y() * canvas_scale_factor) - pos.y()

            self.setScroll(
                Qt.Horizontal,
                self.scrollBars[Qt.Horizontal].value() + x_shift,
            )
            self.setScroll(
                Qt.Vertical,
                self.scrollBars[Qt.Vertical].value() + y_shift,
            )

    def setFitWindow(self, value=True):
        if value:
            self.actions.fitWidth.setChecked(False)
        self.zoomMode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjustScale()

    def setFitWidth(self, value=True):
        if value:
            self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjustScale()

    def togglePolygons(self, value):
        for item, shape in self.labelList.itemsToShapes:
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)

    def loadFiles(self, filenames):
        self.canvas.setEnabled(False)

        if self.max_points is None or self.mesh is None or self.thickness is None:
            dialog = OpenFileDialog()
            if dialog.exec():
                self.max_points, self.mesh, self.thickness = dialog.getInputs()
            else:
                return

        self.rooms = []
        for i, filename in enumerate(filenames):
            self.rooms.append(Room(filename, i, self.mesh, self.thickness, self.max_points))
            item = QtWidgets.QListWidgetItem(self.rooms[-1].filename)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.fileListWidget.addItem(item)
        self.minSliceIdx, self.maxSliceIdx = self.getMinMaxSliceIdx()
        self.lastOpenDir = osp.dirname(self.rooms[-1].filename)
        self.status(self.tr('Loading points from file'))
        self.status(self.tr('Building pixel maps'))
        self.updatePixmap()
        #self.canvas.loadShapes(self.getAllShapes())
        self.setZoomAndScroll()
        self.canvas.setEnabled(True)

        self.paintCanvas()
        self.toggleActions(True)

    def setZoomAndScroll(self):
        is_initial_load = not self.zoom_values
        if self.rooms[0].filename in self.zoom_values:
            self.zoomMode = self.zoom_values[self.rooms[0].filename][0]
            self.setZoom(self.zoom_values[self.rooms[0].filename][1])
        elif is_initial_load or not self._config['keep_prev_scale']:
            self.adjustScale(initial=True)
        # set scroll values
        for orientation in self.scroll_values:
            if self.rooms[0].filename in self.scroll_values[orientation]:
                self.setScroll(
                    orientation, self.scroll_values[orientation][self.rooms[0].filename]
                )

    def updatePixmap(self, idx=None):
        if not self.rooms:
            return
        if self.sliceIdx > self.maxSliceIdx:
            self.sliceIdx = self.minSliceIdx
        if self.sliceIdx < self.minSliceIdx:
            self.sliceIdx = self.maxSliceIdx
        if idx:
            self.canvas.loadImage(idx, self.rooms[idx].images[self.sliceIdx], self.rooms[idx].min_point / self.mesh)
        else:
            self.canvas.loadImages(self.getImages(), self.getImageOffsets())

    def resizeEvent(self, event):
        if self.canvas and self.rooms and self.zoomMode != self.MANUAL_ZOOM:
            self.adjustScale()
        super(MainWindow, self).resizeEvent(event)

    def paintCanvas(self):
        assert self.rooms, "cannot paint null image"
        self.canvas.scale = 0.01 * self.zoomWidget.value()
        self.canvas.adjustSize()
        self.canvas.update()

    def adjustScale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoomMode]()
        value = int(100 * value)
        self.zoomWidget.setValue(value)
        self.zoom_values[self.rooms[0].filename] = (self.zoomMode, value)

    def scaleFitWindow(self):
        """Figure out the size of the pixmap to fit the main widget."""
        # Todo: update this function to calculate scale based on all pixmaps
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.centralWidget().width() - e
        h1 = self.centralWidget().height() - e
        a1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.bounds.width() - 0.0
        h2 = self.canvas.bounds.height() - 0.0
        a2 = w2 / h2
        return w1 / w2 if a2 >= a1 else h1 / h2

    def scaleFitWidth(self):
        # The epsilon does not seem to work too well here.
        w = self.centralWidget().width() - 2.0
        return w / self.canvas.bounds.width()

    def closeEvent(self, event):
        if not self.mayContinue():
            event.ignore()
        self.settings.setValue('filename', '')
        self.settings.setValue('window/size', self.size())
        self.settings.setValue('window/position', self.pos())
        self.settings.setValue('window/state', self.saveState())
        self.settings.setValue('recentFiles', self.recentFiles)
        # ask the use for where to save the labels
        # self.settings.setValue('window/geometry', self.saveGeometry())

    def render3d(self):
        points = np.empty((0, 3))
        for room in self.rooms:
            points = np.append(points, room.points)
        self.pointcloud3d = PointCloud(points)

    def qpointToPointcloud(self, p):
        return (p.x() * self.mesh + self.offset.x(),
                (self.canvas.bounds.height() - p.y()) * self.mesh + self.offset.y())

    def pointcloudToQpoint(self, p):
        x = (p[0] - self.offset.x()) / self.mesh
        y = self.canvas.bounds.height() - ((p[1] - self.offset.y()) / self.mesh)
        return QtCore.QPointF(x, y)

    # User Dialogs #

    def loadRecent(self, filename):
        if self.mayContinue():
            self.loadFiles([filename])

    def showNextSlice(self, _value=False):
        self.sliceIdx += 1
        self.updatePixmap()

    def showLastSlice(self, _value=False):
        self.sliceIdx -= 1
        self.updatePixmap()

    def openPointCloud(self, _value=False):
        if not self.mayContinue():
            return
        formats = ['*.las', '*.laz', '*.pcd', '*.ply', '*.pts']
        filters = self.tr("Point Cloud files (%s)") % ' '.join(
            formats + ['*%s' % LabelFile.suffix])
        filenames, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, self.tr('%s - Choose Point Cloud file') % __appname__, '.', filters)
        if filenames:
            self.loadFiles(filenames)

    def update3dViewer(self, values=None):
        if self.pointcloud3d.viewer_is_ready():
            self.pointcloud3d.render(showing=True)
            if values is not None:
                self.pointcloud3d.viewer.attributes(values)
        else:
            self.toggleActions(viewer=False)

    def saveFileAs(self, _value=False):
        dlg = ChooseFileDialog([room.filename for room in self.rooms])
        if dlg.exec():
            filename = dlg.getSelectedFile()
            if filename is None:
                return
            room = self.getRoomByFilename(filename)
        else:
            return
        point_file = self.saveFileDialog(pointcloud=True)
        label_file = self.saveFileDialog(pointcloud=False)
        self.saveFile(room.index, point_file, label_file)

    def saveFileDialog(self, pointcloud=True):
        if pointcloud:
            caption = self.tr('%s - Choose Point Cloud File') % __appname__
            filters = self.tr('Point Cloud Files (*.las, *.ply, *.pcd, *.xyz, *.pts)')
            default = 'output.las'
        else:
            caption = self.tr('%s - Choose Label File') % __appname__
            filters = self.tr('Label Files (*%s)') % LabelFile.suffix
            default = 'output.json'
        if self.output_dir:
            dlg = QtWidgets.QFileDialog(
                self, caption, self.output_dir, filters
            )
        else:
            dlg = QtWidgets.QFileDialog(
                self, caption, self.currentPath(), filters
            )
        dlg.setOption(QtWidgets.QFileDialog.DontConfirmOverwrite, False)
        dlg.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, False)
        '''
        dlg.setDefaultSuffix(LabelFile.suffix[1:])
        dlg.setAcceptMode(QtWidgets.QFileDialog.AcceptSave)
        dlg.setOption(QtWidgets.QFileDialog.DontConfirmOverwrite, False)
        dlg.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, False)
        basename = osp.basename(osp.splitext(self.rooms[0].filename)[0])
        if self.output_dir:
            default_labelfile_name = osp.join(
                self.output_dir, basename + LabelFile.suffix
            )
        else:
            default_labelfile_name = osp.join(
                self.currentPath(), basename + LabelFile.suffix
            )
        '''
        filename = dlg.getSaveFileName(self, caption, default, filters)
        if QT5:
            filename, _ = filename
        filename = str(filename)
        return filename

    def saveFile(self, idx, point_file=None, label_file=None):
        if point_file is None:
            point_file = self.rooms[idx].pointcloud.filename
        if label_file is None:
            label_file = self.rooms[idx].labelFilename
        self.rooms[idx].save(point_file, label_file)

    def saveFiles(self):
        for room in self.rooms:
            room.save()
        self.setClean()

    def closeFile(self, _value=False):
        if not self.mayContinue():
            return
        self.resetState()
        self.setClean()
        self.toggleActions(False)
        self.canvas.setEnabled(False)
        self.actions.saveAs.setEnabled(False)

    # Message Dialogs. #
    def hasLabels(self):
        if not self.labelList.itemsToShapes:
            self.errorMessage(
                'No objects labeled',
                'You must label at least one object to save the file.')
            return False
        return True

    def mayContinue(self):
        # Todo: update this function
        if not self.dirty:
            return True
        mb = QtWidgets.QMessageBox
        msg = self.tr('Save annotations to "{}" before closing (NOT IMPLEMENTED)?').format(
            self.rooms[0].filename)
        answer = mb.question(self,
                             self.tr('Save annotations?'),
                             msg,
                             mb.Save | mb.Discard | mb.Cancel,
                             mb.Save)
        if answer == mb.Discard:
            return True
        elif answer == mb.Save:
            self.saveFiles()
            return True
        else:  # answer == mb.Cancel
            return False

    def errorMessage(self, title, message):
        return QtWidgets.QMessageBox.critical(
            self, title, '<p><b>%s</b></p>%s' % (title, message))

    def currentPath(self):
        return '.'

    def rotateRoom(self):
        self.canvas.overrideCursor(QtCore.Qt.OpenHandCursor)
        self.canvas.rotating = True
        self.canvas.translating = False

    def translateRoom(self):
        self.canvas.overrideCursor(QtCore.Qt.CrossCursor)
        self.canvas.translating = True
        self.canvas.rotating = False

    def getRoomByFilename(self, filename):
        for room in self.rooms:
            if room.filename == filename:
                return room

    def fileSelectionChanged(self):
        items = self.fileListWidget.selectedItems()
        if items:
            room = self.getRoomByFilename(items[0].text())
            if room:
                self.canvas.roomIdx = room.index
            else:
                self.canvas.roomIdx = None
        else:
            self.canvas.roomIdx = None

    def roomChanged(self, filename):
        room = self.getRoomByFilename(filename)
        room.buildImage(self.sliceIdx)

    def getMinMaxSliceIdx(self):
        min_idx, max_idx = np.inf, -np.inf
        for room in self.rooms:
            min_idx, max_idx = min(min_idx, room.min_idx[2]), max(max_idx, room.max_idx[2])
        return min_idx, max_idx

    def getImages(self):
        images = []
        for room in self.rooms:
            images.append(room.images[self.sliceIdx])
        return images

    def getImageOffsets(self):
        offsets = []
        for room in self.rooms:
            offsets.append(room.min_point / room.mesh)
        return offsets

    def roomRotated(self, idx, angle, cx, cy):
        self.rooms[idx].rotate(angle, np.array((cx, cy)) * self.mesh)
        self.updatePixmap(idx)
        self.setDirty()

    def roomTranslated(self, idx, dx, dy):
        delta = np.array((dx, dy, 0.0)) * self.mesh
        self.rooms[idx].translate(delta)
        self.updatePixmap(idx)
        self.setDirty()

    def setRotating(self):
        self.canvas.rotating, self.canvas.translating = True, False

    def setTranslating(self):
        self.canvas.rotating, self.canvas.translating = False, True
