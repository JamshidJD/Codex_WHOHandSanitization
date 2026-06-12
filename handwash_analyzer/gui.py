"""PyQt5 desktop GUI for WHO Hand Wash Analyzer."""

from __future__ import annotations

import logging
import json
import sys
from pathlib import Path
from typing import Optional

import cv2
from PyQt5.QtCore import QObject, QProcess, QThread, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .utils import OUTPUT_DIR, WHO_STEPS, StepResult, AnalysisReport, setup_logging

LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WHO Hand Wash Analyzer")
        self.resize(1180, 760)
        self.video_path: Optional[str] = None
        self.last_report = None
        self.process: Optional[QProcess] = None
        self.process_output = ""
        self.process_error = ""
        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QGridLayout(root)
        layout.setColumnStretch(0, 3)
        layout.setColumnStretch(1, 2)

        controls = QHBoxLayout()
        self.browse_button = QPushButton("Browse MP4")
        self.start_button = QPushButton("Start Analysis")
        self.stop_button = QPushButton("Stop")
        self.export_button = QPushButton("Open Output Folder")
        self.live_checkbox = QCheckBox("Show Live Processing")
        self.resize_label = QLabel("Resize %")
        self.resize_input = QSpinBox()
        self.resize_input.setRange(25, 100)
        self.resize_input.setSingleStep(5)
        self.resize_input.setValue(50)
        self.resize_input.setToolTip("Resize frames before hand detection. Lower is faster; 100 is full resolution.")
        self.confidence_label = QLabel("Confidence %")
        self.confidence_input = QSpinBox()
        self.confidence_input.setRange(10, 100)
        self.confidence_input.setSingleStep(5)
        self.confidence_input.setValue(80)
        self.confidence_input.setToolTip("Minimum smoothed model confidence required to mark a step complete.")
        self.held_label = QLabel("Held Frames")
        self.held_input = QSpinBox()
        self.held_input.setRange(1, 120)
        self.held_input.setValue(15)
        self.held_input.setToolTip("Consecutive processed frames above confidence threshold required to mark a step complete.")
        self.stop_button.setEnabled(False)
        self.start_button.setEnabled(False)
        controls.addWidget(self.browse_button)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.live_checkbox)
        controls.addWidget(self.resize_label)
        controls.addWidget(self.resize_input)
        controls.addWidget(self.confidence_label)
        controls.addWidget(self.confidence_input)
        controls.addWidget(self.held_label)
        controls.addWidget(self.held_input)
        controls.addStretch(1)
        controls.addWidget(self.export_button)

        self.video_label = QLabel("Select an MP4 video to begin")
        self.video_label.setMinimumSize(720, 480)
        self.video_label.setStyleSheet("background:#111827;color:#d1d5db;border:1px solid #374151;")
        self.video_label.setScaledContents(False)
        self.video_label.setAlignment(QtAlignCenter())

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)

        left = QVBoxLayout()
        left.addLayout(controls)
        left.addWidget(self.video_label, 1)
        left.addWidget(self.progress)

        results_box = QGroupBox("Results")
        results_layout = QVBoxLayout(results_box)
        self.status_label = QLabel("No analysis has run yet.")
        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        results_layout.addWidget(self.status_label)
        results_layout.addWidget(self.results_text, 1)

        layout.addLayout(left, 0, 0)
        layout.addWidget(results_box, 0, 1)

        self.browse_button.clicked.connect(self.browse_video)
        self.start_button.clicked.connect(self.start_analysis)
        self.stop_button.clicked.connect(self.stop_analysis)
        self.export_button.clicked.connect(self.open_output_folder)

    def browse_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select MP4 video", str(Path.home()), "MP4 Videos (*.mp4)")
        if path:
            self.video_path = path
            self.start_button.setEnabled(True)
            self.status_label.setText(f"Selected: {Path(path).name}")
            self._show_first_frame(path)

    def _show_first_frame(self, path: str) -> None:
        cap = cv2.VideoCapture(path)
        ok, frame = cap.read()
        cap.release()
        if ok:
            self._set_frame(frame)

    def start_analysis(self) -> None:
        if not self.video_path:
            return
        self.progress.setValue(0)
        self.results_text.clear()
        self.status_label.setText("Analyzing video...")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.process_output = ""
        self.process_error = ""
        self.process = QProcess(self)
        self.process.setProgram(sys.executable)
        args = ["analyze_cli.py", self.video_path]
        if self.live_checkbox.isChecked():
            args.append("--show-live")
        args.extend(["--processing-scale", f"{self.resize_input.value() / 100:.2f}"])
        args.extend(["--confidence-threshold", f"{self.confidence_input.value() / 100:.2f}"])
        args.extend(["--held-frames", str(self.held_input.value())])
        self.process.setArguments(args)
        self.process.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
        self.process.readyReadStandardOutput.connect(self.on_process_stdout)
        self.process.readyReadStandardError.connect(self.on_process_stderr)
        self.process.finished.connect(self.on_process_finished)
        self.process.start()

    def stop_analysis(self) -> None:
        if self.process:
            self.process.kill()
            self.status_label.setText("Stopping analysis...")

    def on_progress(self, percent: int, frame, label: str, confidence: float) -> None:
        self.progress.setValue(percent)
        self.status_label.setText(f"{label} ({confidence:.2f})")
        self._set_frame(frame)

    def on_finished(self, report, predictions) -> None:
        self.last_report = report
        self.progress.setValue(100)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_label.setText(f"Compliance Score: {report.compliance_percentage:.1f}%")
        self.results_text.setPlainText(self._format_report(report))

    def on_failed(self, message: str) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status_label.setText("Analysis failed")
        self.results_text.setPlainText(message)
        QMessageBox.critical(self, "Analysis failed", message)
        self.process = None

    def on_process_stdout(self) -> None:
        if self.process:
            text = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
            self.process_output += text
            self.status_label.setText("Analyzing video...")

    def on_process_stderr(self) -> None:
        if self.process:
            text = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
            self.process_error += text
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if lines:
                self.status_label.setText(lines[-1][:140])

    def on_process_finished(self, exit_code: int, exit_status) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.process = None
        if exit_code != 0:
            message = self.process_error or self.process_output or f"Analyzer exited with code {exit_code}"
            self.on_failed(message)
            return
        try:
            json_line = next(line for line in reversed(self.process_output.splitlines()) if line.strip().startswith("{"))
            data = json.loads(json_line)
            per_step = {
                label: StepResult(
                    label=value["label"],
                    completed=value["completed"],
                    confidence=value["confidence"],
                    first_frame=value.get("first_frame"),
                    last_frame=value.get("last_frame"),
                )
                for label, value in data["per_step"].items()
            }
            report = AnalysisReport(
                video_path=data["video_path"],
                total_frames=data["total_frames"],
                fps=data["fps"],
                completed_steps=data["completed_steps"],
                missing_steps=data["missing_steps"],
                compliance_percentage=data["compliance_percentage"],
                per_step=per_step,
                predictions_csv=data.get("predictions_csv"),
                annotated_video=data.get("annotated_video"),
            )
            self.progress.setValue(100)
            self.status_label.setText(f"Compliance Score: {report.compliance_percentage:.1f}%")
            self.results_text.setPlainText(self._format_report(report))
        except Exception as exc:
            self.on_failed(f"Could not parse analyzer output: {exc}\n\nSTDOUT:\n{self.process_output}\n\nSTDERR:\n{self.process_error}")

    def _set_frame(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        image = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(image).scaled(self.video_label.size(), aspectRatioMode=1)
        self.video_label.setPixmap(pixmap)

    def _format_report(self, report) -> str:
        lines = ["Completed:"]
        for step in WHO_STEPS:
            result = report.per_step[step]
            mark = "[x]" if result.completed else "[ ]"
            lines.append(f"{mark} {step}  confidence={result.confidence:.2f}")
        lines.append("")
        lines.append(f"Compliance Score: {report.compliance_percentage:.1f}%")
        lines.append(f"Total completed steps: {len(report.completed_steps)} / {len(WHO_STEPS)}")
        lines.append(f"Missing steps: {', '.join(report.missing_steps) if report.missing_steps else 'None'}")
        if report.predictions_csv:
            lines.append(f"Predictions CSV: {report.predictions_csv}")
        if report.annotated_video:
            lines.append(f"Annotated video: {report.annotated_video}")
        return "\n".join(lines)

    def open_output_folder(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = str(OUTPUT_DIR.resolve())
        if sys.platform.startswith("win"):
            import os

            os.startfile(path)
        else:
            QMessageBox.information(self, "Output folder", path)


def QtAlignCenter():
    from PyQt5.QtCore import Qt

    return Qt.AlignCenter


def run_app() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()
