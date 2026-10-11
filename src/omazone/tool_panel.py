"""Fixed tool navigation and primary actions around scrolling settings."""

from PySide6 import QtCore, QtWidgets

from .workflow_steps import STEPS, resolve_step_key


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
        # Step navigation shares the action row, so moving between steps costs no
        # vertical space the viewer needs.
        self.controls = QtWidgets.QWidget()
        self.control_layout = QtWidgets.QHBoxLayout(self.controls)
        self.control_layout.setContentsMargins(0, 0, 0, 0)
        self.control_layout.setSpacing(4)
        self.action_layout.addWidget(self.controls, 0)
        layout.addWidget(self.action_bar)
        self.action_bar.hide()
        self.navigation.currentChanged.connect(self.change_tool)

        self.step_keys = []
        self._label_variants = []
        self._refit_pending = False

    def scheduleRefit(self):
        """Re-pick labels once the layout has applied the new width.

        A resize event arrives before the bar is laid out, so measuring here
        would compare labels against the previous width and keep them too long.
        """
        if self._refit_pending:
            return
        self._refit_pending = True
        QtCore.QTimer.singleShot(0, self._runScheduledRefit)

    def _runScheduledRefit(self):
        self._refit_pending = False
        self.refitNavigation()

    def setLabelVariants(self, variants):
        """Hold roomiest-first label sets and apply whichever fits right now.

        Seven steps carrying their state need about 1085px, which a half-screened
        window does not have. Rather than clip or scroll Export out of sight,
        shorten the bar and keep every step reachable. The state always stays on
        the tooltip, so narrowing the window never loses it.
        """
        self._label_variants = [list(labels) for labels in variants if labels]
        self.refitNavigation()

    def navigationFits(self):
        bar = self.navigation
        visible = [index for index in range(bar.count()) if bar.isTabVisible(index)]
        needed = sum(bar.tabSizeHint(index).width() for index in visible)
        return needed <= bar.width()

    def refitNavigation(self):
        """Re-pick the label set for the current width.

        The short set is expected to fit any usable window, so scrolling is a
        last resort rather than the normal way Export disappears.
        """
        if not self._label_variants:
            return
        labels = self._label_variants[-1]
        for candidate in self._label_variants:
            self._applyLabels(candidate)
            if self.navigationFits():
                labels = candidate
                break
        self._applyLabels(labels)
        self._applyTooltips(self._label_variants[0])

    def _applyLabels(self, labels):
        if len(labels) != len(self.step_keys):
            return
        for index, text in enumerate(labels):
            if self.navigation.tabText(index) != text:
                self.navigation.setTabText(index, text)

    def _applyTooltips(self, labels):
        """The roomiest label doubles as the tooltip, so state is never lost."""
        if len(labels) != len(self.step_keys):
            return
        for index, text in enumerate(labels):
            if self.navigation.tabToolTip(index) != text:
                self.navigation.setTabToolTip(index, text)

    def addTab(self, page, step):
        """Register one page. The step supplies the title, so there is one source."""
        if step is None:
            raise ValueError("Every page must name the workflow step it shows.")
        index = self.stack.addWidget(page)
        self.navigation.addTab(step.title)
        self.navigation.setTabVisible(index, not step.secondary)
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
        """Find a step by its own name or by the comparison name for it."""
        name = resolve_step_key(key)
        return self.step_keys.index(name) if name in self.step_keys else -1

    def setStep(self, key):
        index = self.indexOfStep(key)
        if index >= 0:
            self.setCurrentIndex(index)
        return index >= 0

    def visibleSteps(self):
        """The steps a learner can navigate to, in bar order."""
        return [
            self.step_keys[position]
            for position in range(len(self.step_keys))
            if self.navigation.isTabVisible(position)
        ]

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
