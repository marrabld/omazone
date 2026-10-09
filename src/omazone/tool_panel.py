"""Fixed tool navigation and primary actions around scrolling settings."""

from PySide6 import QtCore, QtWidgets

from .workflow_steps import STEPS


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

        self.step_keys = []

    def addTab(self, page, step):
        """Register one page. The step supplies the title, so there is one source."""
        if step is None:
            raise ValueError("Every page must name the workflow step it shows.")
        index = self.stack.addWidget(page)
        self.navigation.addTab(step.title)
        self.step_keys.append(step.key)
        return index

    def currentWidget(self):
        return self.stack.currentWidget()

    def currentIndex(self):
        return self.navigation.currentIndex()

    def currentStep(self):
        """The step showing now, named rather than numbered.

        The bar is public, so a page added or removed behind the panel's back
        must not turn this into an error or a wrong answer.
        """
        index = self.navigation.currentIndex()
        if 0 <= index < len(self.step_keys):
            return self.step_keys[index]
        return self.step_keys[0] if self.step_keys else STEPS[0].key

    def indexOfStep(self, key):
        try:
            return self.step_keys.index(key)
        except ValueError:
            return -1

    def setStep(self, key):
        index = self.indexOfStep(key)
        if index >= 0:
            self.setCurrentIndex(index)
        return index >= 0

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
