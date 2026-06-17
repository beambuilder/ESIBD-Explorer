import time
from threading import Thread

import cv2
from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGridLayout

from esibd.core import PLUGINTYPE, RestoreIntComboBox
from esibd.plugins import Plugin


def providePlugins() -> 'list[type[Plugin]]':
    """Return list of provided plugins. Indicates that this module provides plugins."""
    return [Webcam]


class Webcam(Plugin):
    """Allows to display the stream from a webcam.

    More advanced use cases where the frames are part of measurement data have to be implemented separately.
    """

    name = 'Webcam'
    version = '1.0'
    pluginType = PLUGINTYPE.CONTROL
    iconFile = 'webcam.png'
    signalComm: 'SignalCommunicate'

    class SignalCommunicate(Plugin.SignalCommunicate):  # signals that can be emitted by external threads
        """Bundle pyqtSignals."""

        frameCaptured = pyqtSignal(object)
        """Emit frame data"""

    def initGUI(self) -> None:
        super().initGUI()
        lay = QGridLayout()
        self.recording = False
        self.graphicsView = QGraphicsView()
        lay.addWidget(self.graphicsView)
        self.addContentLayout(lay)
        self.scene = QGraphicsScene()
        self.graphicsView.setScene(self.scene)
        self.scenePixmapItem = None
        self.signalComm.frameCaptured.connect(self.processFrame)
        self.runThread: 'Thread | None' = None

    def finalizeInit(self) -> None:
        self.recordingAction = self.addStateAction(event=self.toggleRecording,
                                                   toolTipFalse='Start recording.', iconFalse=self.makeIcon('webcam.png'),
                                                   toolTipTrue='Stop recording.', iconTrue=self.makeIcon('webcam.png'))
        super().finalizeInit()
        self.cameraIndexComboBox = RestoreIntComboBox(parentPlugin=self, default='0', items='0,1,2,3,4,5', attr='cameraIndex',
                                                        event=self.cameraIndexChanged, minimum=0, maximum=100, toolTip='Webcam index.')
        if self.titleBar:
            self.titleBar.insertWidget(self.aboutAction, self.cameraIndexComboBox)
        self.copyAction = self.addAction(event=self.copyClipboard, toolTip=f'{self.name} to clipboard.', icon=self.imageClipboardIcon, before=self.aboutAction)

    def copyClipboard(self) -> None:
        if self.scenePixmapItem is not None:
            pixmap = self.scenePixmapItem.pixmap()
            if pixmap:
                self.imageToClipboard(pixmap)

    def cameraIndexChanged(self) -> None:
        """Connect to camera at new index."""
        self.toggleRecording()

    def toggleRecording(self) -> None:
        """Toggle recording on or off."""
        if self.recordingAction.state:
            if self.runThread is not None and self.recording:
                self.recording = False
                self.runThread.join()
            self.recording = True
            self.runThread = Thread(target=self.recordVideo, name=f'{self.name} recordThread')
            self.runThread.daemon = True
            self.runThread.start()
        else:
            self.recording = False

    def processFrame(self, frame: cv2.typing.MatLike) -> None:
        """Convert the frame to a format that Qt can use.

        :param frame: Video frame.
        :type frame: cv2.typing.MatLike
        """
        image = QImage(
            frame.data,
            frame.shape[1],
            frame.shape[0],
            QImage.Format.Format_BGR888,
        )
        pixmap = QPixmap.fromImage(image)

        if self.scenePixmapItem is None:
            self.scenePixmapItem = QGraphicsPixmapItem(pixmap)
            self.scene.addItem(self.scenePixmapItem)
            self.scenePixmapItem.setZValue(0)
            self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.scenePixmapItem.setPixmap(pixmap)

    def fitInView(self, rect: QRectF, aspectRatioMode: Qt.AspectRatioMode) -> None:
        """Scale frame to scene.

        :param rect: The rectangle used by the scene.
        :type rect: QRectF
        :param aspectRatioMode: The aspect ratio used when scaling to scene.
        :type aspectRatioMode: Qt.AspectRatioMode
        """
        self.graphicsView.fitInView(rect, aspectRatioMode)

    def recordVideo(self) -> None:
        """Record video frames in parallel thread."""
        cap = cv2.VideoCapture(int(self.cameraIndexComboBox.currentText()))
        while self.recording:
            ret, frame = cap.read()
            if ret:
                self.signalComm.frameCaptured.emit(frame)
                time.sleep(0.033)  # Limit to ~30 FPS
            else:
                break
        cap.release()
        self.scenePixmapItem = None
