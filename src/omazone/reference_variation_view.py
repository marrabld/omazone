"""Optional measured reference envelope, independent of matching/render settings."""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets
from scipy.interpolate import PchipInterpolator

from .reference_variation import DB_EDGES, FLOOR_DB, MIN_WINDOWS, band_density


def smooth_display(centres, values, x, edges=None):
    """Shape-preserving interpolation in log frequency, never across missing bands."""
    result = np.full(x.shape, np.nan)
    supported = np.flatnonzero(np.isfinite(values))
    for run in np.split(supported, np.flatnonzero(np.diff(supported) > 1) + 1):
        if not len(run):
            continue
        left, right = centres[run[[0, -1]]]
        mask = (x >= (left if run[0] else -np.inf)) & (
            x <= (right if run[-1] != len(centres) - 1 else np.inf)
        )
        if len(run) == 1:
            if edges is not None:
                mask = (x >= edges[run[0]]) & (x <= edges[run[0] + 1])
            result[mask] = values[run[0]]
        else:
            result[mask] = PchipInterpolator(centres[run], values[run])(
                np.clip(x[mask], left, right)
            )
    return result


def display_envelope(variation, x):
    centres = np.log10(variation.frequency)

    def interpolate(values):
        return smooth_display(
            centres, np.where(variation.valid, values, np.nan), x, np.log10(variation.edges)
        )

    median = interpolate(variation.median_db)
    # Interpolate nonnegative distances so boundaries cannot cross the median.
    lower = median - interpolate(variation.median_db - variation.lower_db)
    upper = median + interpolate(variation.upper_db - variation.median_db)
    return lower, median, upper


def density_image(variation):
    """Empirical 1 dB bin mass, interpolated in log frequency for display only.

    One shared 0..1 scale across frequency bands. Square-root opacity makes
    sparse observations visible without independently auto-stretching each band.
    """
    # Subdivide the display pixels, not the measured histogram bins. This keeps
    # the sloping percentile mask from becoming a staircase at normal zoom.
    width = len(variation.valid) * 64
    subdivisions = 8
    x0, x1 = np.log10(variation.edges[[0, -1]])
    x = x0 + (np.arange(width) + 0.5) * (x1 - x0) / width
    centres = np.log10(variation.frequency)
    mass = variation.counts / np.maximum(variation.band_counts[:, None], 1)
    mass = np.where(variation.valid[:, None], mass, np.nan)
    density = np.array([np.interp(x, centres, row) for row in mass.T]).T
    isolated = (
        variation.valid & ~np.r_[False, variation.valid[:-1]] & ~np.r_[variation.valid[1:], False]
    )
    for band in np.flatnonzero(isolated):
        # One supported band has no neighbour to interpolate toward. Its
        # histogram describes its own bandwidth, not an invisible single point.
        mask = (x >= np.log10(variation.edges[band])) & (x <= np.log10(variation.edges[band + 1]))
        density[mask] = mass[band]
    density = np.repeat(density, subdivisions, axis=1)
    lower, _, upper = display_envelope(variation, x)
    display_edges = np.linspace(DB_EDGES[0], DB_EDGES[-1], len(density[0]) + 1)
    overlap = np.clip(
        (
            np.minimum(display_edges[1:], upper[:, None])
            - np.maximum(display_edges[:-1], lower[:, None])
        )
        * subdivisions,
        0,
        1,
    )
    alpha = np.nan_to_num(np.sqrt(density) * overlap, nan=0) * 210
    rgba = np.empty((*alpha.shape, 4), dtype=np.uint8)
    rgba[..., :3] = (234, 187, 107)
    rgba[..., 3] = np.rint(alpha).astype(np.uint8)
    return rgba


class SmoothDensityImage(pg.ImageItem):
    """Smooth raster scaling without changing global plot rendering settings."""

    def paint(self, painter, *args):
        painter.save()
        try:
            painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
            super().paint(painter, *args)
        finally:
            painter.restore()


class ReferenceVariationView(QtWidgets.QGroupBox):
    def __init__(self, owner):
        super().__init__("Reference variation")
        self.owner = owner
        self.image = None
        # One-entry identity cache, holding only the small analysis and raster.
        # Plot.clear() detaches the item; it can be reused on the next redraw.
        self._density_variation = None
        self._density_item = None
        # Keep the small profile alive while its id is in the spectrum key.
        # Otherwise replacing a target can reuse that id and leave an old image.
        self.target = None
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.show_range = QtWidgets.QCheckBox("Show envelope and density")
        self.show_range.setChecked(True)
        layout.addWidget(self.show_range)
        self.note = QtWidgets.QLabel()
        self.note.setWordWrap(True)
        self.note.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.note)
        self.setToolTip(
            "Observed variation within one reference passage, not a confidence interval or an ideal tonal balance. The display never changes EQ matching or export."
        )
        self.show_range.toggled.connect(self.changed)
        self.hide()

    def changed(self):
        workspace = self.owner.workspace
        self.owner.spectrum_plot.clear()
        self.image = None
        self.refresh()
        if (
            workspace.cached_spectrum is not None
            and workspace.cached_spectrum[0] == workspace.completed_key
            and workspace.completed_key == workspace.requested_key
        ):
            workspace.spectrum_ready(workspace.cached_spectrum)
            workspace.timer.start()  # Revalidate context if a target changed during the redraw.
            return
        workspace.completed_key = workspace.requested_key = None
        workspace.timer.start()

    def refresh(self):
        visible = self.owner.views.currentStep() in ("match", "reference", "sections")
        self.setVisible(visible)
        target = self.owner.workspace.reference_target() if visible else None
        if self.target is not target:
            if self.image is not None:
                self.owner.spectrum_plot.clear()
                self.image = None
            self.target = target
        variation = target.variation if target is not None else None
        self.show_range.setEnabled(variation is not None and variation.available)
        if target is None:
            text = "Load a reference or select a captured target to see its measured variation. Display only; ordinary EQ matching is unchanged."
        elif variation is None:
            text = "No variation data in this target. Reload a reference or capture at least four full seconds. Mean-only targets remain usable for EQ matching."
        elif not variation.available:
            text = f"Variation unavailable: {variation.accepted_windows}/{variation.sampled_windows} excerpts passed the energy gates; bands need at least {MIN_WINDOWS} above-floor observations and 80% coverage."
        else:
            start, end = (
                target.region.start / target.sample_rate,
                target.region.end / target.sample_rate,
            )
            text = (
                f"1 reference passage: {target.name}, {start:.2f}-{end:.2f} s. "
                f"{variation.accepted_windows}/{variation.sampled_windows} sampled 1 s excerpts passed the gates. "
                "10-90% range; dashed median. Brighter = larger fraction per 1 dB bin. "
                "Within-passage variation, not confidence. Display only; EQ is unchanged."
            )
        self.note.setToolTip(
            "Each band's histogram is divided by its number of above-floor observations, on one shared 0..1 scale with square-root opacity. Frequency shading is interpolated for display. Blank bands have insufficient observations, energy or FFT resolution. Spectra are band-averaged relative power density, not dBFS or a measure of musical quality."
        )
        self.note.setText(text)

    def draw(self, before, after, target):
        plot = self.owner.spectrum_plot
        self.image = None
        if (
            self.owner.views.currentStep() not in ("match", "reference", "sections")
            or not self.show_range.isChecked()
            or target is None
            or target.variation is None
            or not target.variation.available
        ):
            plot.setLabel("left", "Power", units="dB")
            return False
        variation = target.variation
        if self._density_variation is not variation:
            self._density_item = SmoothDensityImage(density_image(variation), axisOrder="col-major")
            self._density_variation = variation
        self.image = self._density_item
        x0, x1 = np.log10(variation.edges[[0, -1]])
        self.image.setRect(QtCore.QRectF(x0, FLOOR_DB, x1 - x0, -FLOOR_DB))
        self.image.setZValue(-10)
        plot.addItem(self.image, ignoreBounds=True)
        centres = np.log10(variation.frequency)
        edges = np.log10(variation.edges)
        # Include measured centres exactly, alongside dense display-only samples.
        x = np.unique(np.r_[centres, edges, np.linspace(edges[0], edges[-1], len(centres) * 64)])
        frequency = 10**x
        lower, median, upper = display_envelope(variation, x)
        curves = []
        for values in (lower, upper):
            curves.append(
                plot.plot(
                    frequency,
                    values,
                    pen=pg.mkPen((234, 187, 107, 160), width=1),
                    connect="finite",
                    antialias=True,
                )
            )
        fill = pg.FillBetweenItem(curves[0], curves[1], brush=pg.mkBrush(234, 187, 107, 18))
        fill.setZValue(-11)
        plot.addItem(fill)
        plot.plot(
            frequency,
            median,
            pen=pg.mkPen("#eabb6b", width=1.5, style=QtCore.Qt.PenStyle.DashLine),
            name="Reference median",
            connect="finite",
            antialias=True,
        )
        before_name = (
            "Original" if self.owner.workspace.signal.currentData() == "original" else "Step input"
        )
        for spectrum, name, colour in (
            (before, before_name, "#73a8ff"),
            (after, "Step output", "#63dfc0"),
            (target.spectrum, "Reference mean", "#d8e1ed"),
        ):
            if spectrum is not None:
                style = (
                    QtCore.Qt.PenStyle.DotLine
                    if name == "Reference mean"
                    else QtCore.Qt.PenStyle.DashDotLine
                    if name == "Step output"
                    else QtCore.Qt.PenStyle.SolidLine
                )
                plot.plot(
                    frequency,
                    smooth_display(centres, band_density(spectrum, variation.edges), x, edges),
                    pen=pg.mkPen(colour, width=1.5, style=style),
                    name=name,
                    connect="finite",
                    antialias=True,
                )
        plot.setLabel("left", "Power", units="dB/Hz")
        plot.setTitle("Reference variation | band-averaged spectra")
        return True
