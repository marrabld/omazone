"""Fixed tool navigation and primary actions around scrolling settings."""

from PySide6 import QtCore, QtWidgets


class CurrentPageStack(QtWidgets.QStackedWidget):
    """Size scrolling content for the selected tool, not the largest hidden page."""

    def sizeHint(self):
        page = self.currentWidget()
        return page.sizeHint() if page is not None else super().sizeHint()

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else super().minimumSizeHint()


class ToolPanel(QtWidgets.QWidget):
    currentChanged = QtCore.Signal(int)

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.navigation = QtWidgets.QTabBar()
        self.navigation.setExpanding(False)
        self.navigation.setUsesScrollButtons(True)
        layout.addWidget(self.navigation)
        self.stack = CurrentPageStack()
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.scroll_area.setWidget(self.stack)
        layout.addWidget(self.scroll_area, 1)
        self.action_bar = QtWidgets.QWidget()
        self.action_layout = QtWidgets.QHBoxLayout(self.action_bar)
        self.action_layout.setContentsMargins(4, 4, 4, 4)
        self.action_label = QtWidgets.QLabel()
        self.action_label.setWordWrap(True)
        self.action_layout.addWidget(self.action_label, 1)
        layout.addWidget(self.action_bar)
        self.action_bar.hide()
        self.navigation.currentChanged.connect(self.change_tool)

    def addTab(self, page, title):
        index = self.stack.addWidget(page)
        self.navigation.addTab(title)
        return index

    def currentWidget(self):
        return self.stack.currentWidget()

    def currentIndex(self):
        return self.navigation.currentIndex()

    def count(self):
        return self.stack.count()

    def setCurrentIndex(self, index):
        self.navigation.setCurrentIndex(index)

    def setCurrentWidget(self, page):
        index = self.stack.indexOf(page)
        if index >= 0:
            self.setCurrentIndex(index)

    def change_tool(self, index):
        self.stack.setCurrentIndex(index)
        self.stack.updateGeometry()
        self.scroll_area.verticalScrollBar().setValue(0)
        self.currentChanged.emit(index)
