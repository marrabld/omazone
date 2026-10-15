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

    def heightForWidth(self, width):
        # QStackedLayout otherwise budgets for hidden pages too. That creates
        # empty space before shared inspector content below the selected page.
        page = self.currentWidget()
        if page is None:
            return super().heightForWidth(width)
        height = page.heightForWidth(width)
        return height if height >= 0 else page.sizeHint().height()


class InspectorContent(QtWidgets.QWidget):
    def child_heights(self, width):
        # Ask each widget explicitly: Qt's QWidgetItem can bypass a stack's
        # override and ask its all-pages QStackedLayout for height-for-width.
        layout = self.layout()
        margins = layout.contentsMargins()
        heights = []
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is not None and not widget.isHidden():
                height = widget.heightForWidth(width - margins.left() - margins.right())
                heights.append((widget, max(
                    widget.minimumSizeHint().height(),
                    height if height >= 0 else widget.sizeHint().height(),
                )))
        return heights

    def heightForWidth(self, width):
        layout = self.layout()
        margins = layout.contentsMargins()
        heights = self.child_heights(width)
        return (
            sum(height for _, height in heights) + max(0, len(heights) - 1) * layout.spacing()
            + margins.top() + margins.bottom()
        )

    def fit_children(self):
        if self.layout() is None or getattr(self, "fitting", False):
            return
        self.fitting = True
        try:
            # Budgeting the correct total is not enough: QBoxLayout can still
            # give a hidden-page stack extra space by squeezing shared panels.
            # Make each visible child's actual wrapped height its minimum.
            for widget, height in self.child_heights(self.width()):
                if widget.minimumHeight() != height:
                    widget.setMinimumHeight(height)
        finally:
            self.fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_children()

    def event(self, event):
        if event.type() == QtCore.QEvent.Type.LayoutRequest:
            self.fit_children()
        return super().event(event)


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
        self.settings_content = InspectorContent()
        self.settings_layout = QtWidgets.QVBoxLayout(self.settings_content)
        self.settings_layout.setContentsMargins(0, 0, 0, 0)
        self.settings_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.settings_layout.addWidget(self.stack)
        self.scroll_area.setWidget(self.settings_content)
        layout.addWidget(self.scroll_area, 1)
        self.action_bar = QtWidgets.QWidget()
        self.action_layout = QtWidgets.QHBoxLayout(self.action_bar)
        self.action_layout.setContentsMargins(4, 4, 4, 4)
        self.action_label = QtWidgets.QLabel()
        self.action_label.setWordWrap(True)
        self.action_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
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
