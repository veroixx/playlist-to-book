import sys
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QProgressBar, QTextEdit, QFileDialog, QMessageBox,
                             QComboBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from datetime import datetime
from pytube import Playlist
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound
from groq import Groq
import re
import logging
import os
import time
import random
from dotenv import load_dotenv
CHUNK_SIZE = 3000

load_dotenv(".env")

BOOK_PROMPT = """Transform the following transcript into a well-structured book chapter.

STRICT RULES — follow these exactly:
- Only use information explicitly present in the transcript. Do not add, invent, or infer \
any facts, examples, or explanations that are not directly stated.
- Do not summarize or compress — retain every detail, explanation, and example from the transcript.
- If the speaker defines or explains something, preserve that explanation fully in their intended meaning.

FORMATTING:
- Use clear headings and subheadings to organize the content.
- Use bullet points or numbered lists where the transcript presents steps, lists, or comparisons.
- For any technical term or concept that appears but is not explained in the transcript, \
add a short blockquote definition (max 2 sentences) sourced from general knowledge — \
clearly marked as a note, e.g.: > **Note:** [definition]
- Keep the tone clear and instructional, suitable for a technical book.

All output must be in [Language] only. Do not include the original transcript text in your response.

Transcript:"""

def _find_cookies():
    """Look for cookies.txt next to main.py or in the working directory."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt"),
        os.path.join(os.getcwd(), "cookies.txt"),
        "cookies.txt",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

COOKIES_FILE = _find_cookies()


AVAILABLE_MODELS = [
    "llama-3.3-70b-versatile",          # best free option
    "llama-3.1-8b-instant",             # fastest
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        self.extraction_thread = None
        self.book_thread = None
        self.is_processing = False
        self.selected_model = "llama-3.3-70b-versatile"

        self.initUI()

    # ------------------------------------------------------------------ UI --

    def initUI(self):
        self.setWindowTitle("YouTube Playlist → Book Generator")
        self.setMinimumSize(900, 700)
        self.apply_light_theme()
        self.showFullScreen()

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────────────
        header = QWidget()
        header.setFixedHeight(68)
        header.setStyleSheet("background-color: #1c1c2e;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(40, 0, 40, 0)

        icon_lbl = QLabel("📖")
        icon_lbl.setFont(QFont("Segoe UI", 20))
        icon_lbl.setStyleSheet("background: transparent; color: white;")

        app_title = QLabel("Playlist to Book")
        app_title.setFont(QFont("Georgia", 17, QFont.Bold))
        app_title.setStyleSheet("background: transparent; color: #e8d5b7; letter-spacing: 1px;")

        app_sub = QLabel("YouTube → Markdown Book Generator")
        app_sub.setFont(QFont("Segoe UI", 9))
        app_sub.setStyleSheet("background: transparent; color: #7a7a9a;")

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title_col.addWidget(app_title)
        title_col.addWidget(app_sub)

        header_layout.addWidget(icon_lbl)
        header_layout.addSpacing(12)
        header_layout.addLayout(title_col)
        header_layout.addStretch()
        main_layout.addWidget(header)

        # ── Body ─────────────────────────────────────────────────────────────
        body = QWidget()
        body.setStyleSheet("background-color: #f2f0eb;")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(36, 28, 36, 28)
        body_layout.setSpacing(24)

        # ── Left panel: inputs ───────────────────────────────────────────────
        left = QWidget()
        left.setStyleSheet(
            "background: white; border-radius: 12px; border: 1px solid #dedad2;"
        )
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(28, 22, 28, 22)
        left_layout.setSpacing(3)

        self._section_label(left_layout, "SOURCE")
        self._field_label(left_layout, "YouTube URL")
        self.url_input = self._make_line_edit("https://www.youtube.com/playlist?list=...")
        left_layout.addWidget(self.url_input)
        left_layout.addSpacing(8)

        self._field_label(left_layout, "Book Title")
        self.book_title_input = self._make_line_edit("e.g. Building LLMs From Scratch")
        left_layout.addWidget(self.book_title_input)
        left_layout.addSpacing(8)

        self._field_label(left_layout, "Output Language")
        self.language_input = self._make_line_edit("English")
        self.language_input.setText(os.environ.get("LANGUAGE", "English"))
        left_layout.addWidget(self.language_input)
        left_layout.addSpacing(16)

        self._section_label(left_layout, "OUTPUT FILES")
        self._field_label(left_layout, "Transcript Cache")
        self.transcript_file_input = self._make_file_row(
            left_layout, "Select transcript cache path...", self.select_transcript_file
        )
        left_layout.addSpacing(8)

        self._field_label(left_layout, "Book Output File")
        self.book_file_input = self._make_file_row(
            left_layout, "Select book output path...", self.select_book_output_file
        )
        left_layout.addSpacing(16)

        self._section_label(left_layout, "CREDENTIALS")
        self._field_label(left_layout, "Groq API Key")
        self.api_key_input = self._make_line_edit("Paste your API key here")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setText(os.environ.get("API_KEY", ""))
        left_layout.addWidget(self.api_key_input)

        left_layout.addStretch()
        left_layout.addSpacing(16)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.generate_button = QPushButton("  Generate Book")
        self.generate_button.setFont(QFont("Georgia", 11, QFont.Bold))
        self.generate_button.setFixedHeight(44)
        self.generate_button.setCursor(Qt.PointingHandCursor)
        self.generate_button.setStyleSheet("""
            QPushButton {
                background-color: #1c1c2e;
                color: #e8d5b7;
                border: none;
                border-radius: 8px;
                padding: 0 28px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #2e2e50; }
            QPushButton:disabled { background-color: #c5c1b8; color: #9a9690; }
        """)
        self.generate_button.clicked.connect(self.start_processing)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setFont(QFont("Segoe UI", 10))
        self.cancel_button.setFixedHeight(44)
        self.cancel_button.setCursor(Qt.PointingHandCursor)
        self.cancel_button.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #888;
                border: 1.5px solid #ccc;
                border-radius: 8px;
                padding: 0 20px;
            }
            QPushButton:hover { border-color: #c0392b; color: #c0392b; }
            QPushButton:disabled { color: #ccc; border-color: #e5e5e5; }
        """)
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.cancel_button.setEnabled(False)

        btn_row.addWidget(self.generate_button)
        btn_row.addWidget(self.cancel_button)
        left_layout.addLayout(btn_row)

        # Skip extraction button
        self.skip_button = QPushButton("📄  I Already Have the Transcript")
        self.skip_button.setFont(QFont("Segoe UI", 9))
        self.skip_button.setFixedHeight(36)
        self.skip_button.setCursor(Qt.PointingHandCursor)
        self.skip_button.setStyleSheet("""
            QPushButton {
                background: #f0ede8;
                color: #3a3530;
                border: 1.5px solid #c8c3b8;
                border-radius: 8px;
                padding: 0 16px;
                font-weight: 600;
            }
            QPushButton:hover { background: #e5e0d8; border-color: #1c1c2e; }
            QPushButton:disabled { color: #bbb; border-color: #e5e5e5; }
        """)
        self.skip_button.clicked.connect(self.start_from_transcript)
        left_layout.addWidget(self.skip_button)

        # ── Right panel: progress + log ──────────────────────────────────────
        right = QWidget()
        right.setStyleSheet(
            "background: white; border-radius: 12px; border: 1px solid #dedad2;"
        )
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(28, 22, 28, 22)
        right_layout.setSpacing(10)

        self._section_label(right_layout, "PROGRESS")

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background: #eae6de;
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1c1c2e, stop:1 #4a4a8a);
                border-radius: 4px;
            }
        """)
        right_layout.addWidget(self.progress_bar)

        self.progress_pct = QLabel("0%")
        self.progress_pct.setFont(QFont("Segoe UI", 9, QFont.Bold))
        self.progress_pct.setStyleSheet("color: #1c1c2e; background: transparent;")
        self.progress_pct.setAlignment(Qt.AlignRight)
        right_layout.addWidget(self.progress_pct)

        right_layout.addSpacing(6)
        self._section_label(right_layout, "LOG")

        self.status_display = QTextEdit()
        self.status_display.setReadOnly(True)
        self.status_display.setFont(QFont("Courier New", 9))
        self.status_display.setStyleSheet("""
            QTextEdit {
                background-color: #faf8f4;
                border: 1px solid #e0dbd0;
                border-radius: 8px;
                color: #2a2a2a;
                padding: 12px;
            }
            QScrollBar:vertical {
                background: #f0ede6; width: 7px; border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #c8c3b8; border-radius: 3px;
            }
        """)
        right_layout.addWidget(self.status_display)

        body_layout.addWidget(left, 40)
        body_layout.addWidget(right, 60)
        main_layout.addWidget(body)

        self.central_widget.setLayout(main_layout)
        self.center()

    # ─── UI helpers ──────────────────────────────────────────────────────────

    def _section_label(self, layout, text):
        lbl = QLabel(text)
        lbl.setFont(QFont("Segoe UI", 7, QFont.Bold))
        lbl.setStyleSheet(
            "color: #aaa49a; letter-spacing: 2px; background: transparent; padding-top: 8px;"
        )
        layout.addWidget(lbl)
        layout.addSpacing(2)

    def _add_label(self, layout, text):
        self._field_label(layout, text)

    def _field_label(self, layout, text):
        lbl = QLabel(text)
        lbl.setFont(QFont("Segoe UI", 10))
        lbl.setStyleSheet("color: #3a3530; background: transparent; font-weight: 600;")
        layout.addWidget(lbl)

    def _make_line_edit(self, placeholder):
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setFont(QFont("Segoe UI", 10))
        field.setFixedHeight(38)
        field.setStyleSheet("""
            QLineEdit {
                background: #faf8f4;
                border: 1.5px solid #dbd6cc;
                border-radius: 7px;
                color: #1e1e1e;
                padding: 0 10px;
                selection-background-color: #c5c0f0;
            }
            QLineEdit:focus { border-color: #1c1c2e; background: white; }
            QLineEdit:disabled { background: #efece6; color: #aaa; }
        """)
        return field

    def _make_file_row(self, parent_layout, placeholder, handler):
        row = QHBoxLayout()
        row.setSpacing(8)
        field = QLineEdit()
        field.setReadOnly(True)
        field.setPlaceholderText(placeholder)
        field.setFont(QFont("Segoe UI", 9))
        field.setFixedHeight(38)
        field.setStyleSheet("""
            QLineEdit {
                background: #faf8f4;
                border: 1.5px solid #dbd6cc;
                border-radius: 7px;
                color: #1e1e1e;
                padding: 0 10px;
            }
            QLineEdit:disabled { background: #efece6; color: #aaa; }
        """)
        btn = QPushButton("Browse")
        btn.setFixedSize(80, 38)
        btn.setFont(QFont("Segoe UI", 9))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("""
            QPushButton {
                background: #edeae3;
                color: #3a3530;
                border: 1.5px solid #dbd6cc;
                border-radius: 7px;
                font-weight: 600;
            }
            QPushButton:hover { background: #e2ddd5; border-color: #1c1c2e; }
        """)
        btn.clicked.connect(handler)
        row.addWidget(field)
        row.addWidget(btn)
        parent_layout.addLayout(row)
        return field

    def get_input_style(self):
        return ""

    def get_button_style(self, c1, c2):
        return ""

    def apply_light_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #f2f0eb; }
            QWidget { font-family: 'Segoe UI'; }
        """)

    def apply_dark_mode(self):
        self.apply_light_theme()

    def center(self):
        frame = self.frameGeometry()
        center_point = QApplication.primaryScreen().availableGeometry().center()
        frame.moveCenter(center_point)
        self.move(frame.topLeft())

    # --------------------------------------------------------- validation ---

    def validate_inputs(self):
        def warn(text, title):
            mb = QMessageBox()
            mb.setStyleSheet("color: #ecf0f1; background-color: #34495e;")
            mb.setIcon(QMessageBox.Warning)
            mb.setText(text)
            mb.setWindowTitle(title)
            mb.exec_()

        url = self.url_input.text().strip()
        if not (url.startswith("https://www.youtube.com/playlist") or
                url.startswith("https://www.youtube.com/watch?v=")):
            warn("Please enter a valid YouTube playlist or video URL.", "Invalid URL")
            return False

        if not self.book_title_input.text().strip():
            warn("Please enter a title for your book.", "Book Title Required")
            return False

        if not self.transcript_file_input.text().endswith(".txt"):
            warn("Please select a .txt file for the transcript cache.", "Invalid File")
            return False

        if not self.book_file_input.text().endswith(".txt"):
            warn("Please select a .txt file for the book output.", "Invalid File")
            return False

        if not self.api_key_input.text().strip():
            warn("Please enter your Gemini API key.", "API Key Required")
            return False

        if not self.language_input.text().strip():
            warn("Please specify the output language.", "Language Required")
            return False

        return True

    # -------------------------------------------------- model selection ----

    def select_gemini_model(self):
        mb = QMessageBox()
        mb.setStyleSheet("color: #ecf0f1; background-color: #34495e;")
        mb.setWindowTitle("Select Groq Model")
        mb.setText("Choose a Groq model:")

        combo = QComboBox()
        combo.addItems(AVAILABLE_MODELS)
        combo.setCurrentText(self.selected_model)

        container = QWidget()
        container.setLayout(QVBoxLayout())
        container.layout().addWidget(combo)
        mb.layout().addWidget(container, 1, 0, mb.layout().rowCount(), 1)

        ok_btn = mb.addButton(QMessageBox.Ok)
        mb.addButton(QMessageBox.Cancel)
        mb.exec_()

        return combo.currentText() if mb.clickedButton() == ok_btn else None

    # --------------------------------------------------- processing flow ---

    def start_from_transcript(self):
        """Skip extraction — go straight to book generation using existing transcript."""
        transcript_path = self.transcript_file_input.text().strip()

        if not transcript_path or not os.path.exists(transcript_path):
            mb = QMessageBox()
            mb.setStyleSheet("color: #ecf0f1; background-color: #34495e;")
            mb.setIcon(QMessageBox.Warning)
            mb.setText("Please select an existing transcript file using the 'Browse' button next to Transcript Cache.")
            mb.setWindowTitle("No Transcript File")
            mb.exec_()
            return

        if not self.book_file_input.text().strip():
            mb = QMessageBox()
            mb.setStyleSheet("color: #ecf0f1; background-color: #34495e;")
            mb.setIcon(QMessageBox.Warning)
            mb.setText("Please select a Book Output File path.")
            mb.setWindowTitle("No Output File")
            mb.exec_()
            return

        if not self.api_key_input.text().strip():
            mb = QMessageBox()
            mb.setStyleSheet("color: #ecf0f1; background-color: #34495e;")
            mb.setIcon(QMessageBox.Warning)
            mb.setText("Please enter your Groq API key.")
            mb.setWindowTitle("API Key Required")
            mb.exec_()
            return

        # Book output file is optional — auto-generated if missing
        model = self.select_gemini_model()
        if not model:
            return
        self.selected_model = model

        self.set_processing_state(True)
        self.progress_bar.setValue(0)
        self.status_display.clear()
        self.log_info(f"Using existing transcript: {transcript_path}")
        self.start_book_generation(transcript_path)

    def start_processing(self):
        if not self.validate_inputs():
            return

        model = self.select_gemini_model()
        if not model:
            return
        self.selected_model = model

        self.set_processing_state(True)
        self.progress_bar.setValue(0)
        self.status_display.clear()

        transcript_path = self.transcript_file_input.text() or \
            f"transcript_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"

        self.extraction_thread = TranscriptExtractionThread(
            self.url_input.text().strip(),
            transcript_path
        )
        self.extraction_thread.progress_update.connect(self.progress_bar.setValue)
        self.extraction_thread.status_update.connect(self.log)
        self.extraction_thread.extraction_complete.connect(self.start_book_generation)
        self.extraction_thread.error_occurred.connect(self.handle_error)

        self.log_info("Starting transcript extraction...")
        self.extraction_thread.start()

    def start_book_generation(self, transcript_file):
        self.progress_bar.setValue(0)
        self.log_info("Transcripts fetched. Starting book generation...")

        output_file = self.book_file_input.text().strip()
        if not output_file:
            # Auto-generate output path next to transcript file
            base = os.path.splitext(transcript_file)[0]
            output_file = base + "_book.txt"
            self.book_file_input.setText(output_file)
            self.log_info(f"No output file set — saving to: {output_file}")

        book_title = self.book_title_input.text().strip() or "Untitled Book"

        self.book_thread = BookGenerationThread(
            transcript_file=transcript_file,
            output_file=output_file,
            api_key=self.api_key_input.text().strip(),
            model_name=self.selected_model,
            language=self.language_input.text().strip(),
            book_title=book_title,
        )
        self.book_thread.progress_update.connect(self.progress_bar.setValue)
        self.book_thread.status_update.connect(self.log)
        self.book_thread.complete.connect(self.handle_success)
        self.book_thread.error_occurred.connect(self.handle_error)
        self.book_thread.start()

    def set_processing_state(self, processing):
        self.is_processing = processing
        self.generate_button.setEnabled(not processing)
        self.cancel_button.setEnabled(processing)
        self.skip_button.setEnabled(not processing)
        for field in [self.url_input, self.book_title_input, self.language_input,
                      self.transcript_file_input, self.book_file_input, self.api_key_input]:
            field.setReadOnly(processing)

    def handle_success(self, output_file):
        self.set_processing_state(False)
        self.progress_bar.setValue(100)
        mb = QMessageBox()
        mb.setStyleSheet("color: #ecf0f1; background-color: #34495e;")
        mb.setIcon(QMessageBox.Information)
        mb.setText(f"Book generation complete!\nSaved to: {output_file}")
        mb.setWindowTitle("Done")
        mb.exec_()

    def handle_error(self, error):
        self.set_processing_state(False)
        self.progress_bar.setValue(0)
        mb = QMessageBox()
        mb.setStyleSheet("color: #ecf0f1; background-color: #34495e;")
        mb.setIcon(QMessageBox.Critical)
        mb.setText(error)
        mb.setWindowTitle("Error")
        mb.exec_()

    def cancel_processing(self):
        for thread in [self.extraction_thread, self.book_thread]:
            if thread and thread.isRunning():
                thread.stop()
                thread.quit()
                thread.wait()
        self.set_processing_state(False)
        self.status_display.append("<font color='#c0392b'>Cancelled.</font>")
        self.progress_bar.setValue(0)

    # -------------------------------------------------------- file dialogs --

    def select_transcript_file(self):
        """Open an EXISTING transcript - read only, never overwrites."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Existing Transcript File", "", "Text Files (*.txt);;All Files (*)"
        )
        if path:
            self.transcript_file_input.setText(path)

    def select_book_output_file(self):
        """Choose where to SAVE the generated book."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Select Book Output File", "", "Text Files (*.txt);;All Files (*)"
        )
        if path:
            if not path.endswith(".txt"):
                path += ".txt"
            self.book_file_input.setText(path)

    # --------------------------------------------------------- logging -----

    def log(self, message):
        self.status_display.append(f"<font color='#2a7a3b'>{message}</font>")
        if hasattr(self, "progress_pct"):
            self.progress_pct.setText(f"{self.progress_bar.value()}%")

    def log_info(self, message):
        self.status_display.append(f"<font color='#1c1c6e'>{message}</font>")


# ============================================================ Threads =======

class TranscriptExtractionThread(QThread):
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    extraction_complete = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, playlist_url, output_file):
        super().__init__()
        self.playlist_url = playlist_url
        self.output_file = output_file
        self._is_running = True

    def run(self):
        try:
            url = self.playlist_url
            if "playlist?list=" in url:
                playlist = Playlist(url)
                video_urls = list(playlist.video_urls)
                total_videos = len(video_urls)
                playlist_name = playlist.title
            elif "watch?v=" in url:
                video_urls = [url]
                total_videos = 1
                playlist_name = "Single Video"
            else:
                self.error_occurred.emit("Invalid URL. Please use a YouTube video or playlist URL.")
                return

            cookies = COOKIES_FILE
            if cookies:
                self.status_update.emit(f"Using cookies: {cookies}")
            else:
                self.status_update.emit("No cookies.txt found — running without authentication.")

            with open(self.output_file, "w", encoding="utf-8") as f:
                f.write(f"Playlist Name: {playlist_name}\n\n")
                for index, video_url in enumerate(video_urls, 1):
                    if not self._is_running:
                        return
                    try:
                        transcript = self._fetch_transcript(video_url, index, total_videos, cookies)
                        if transcript:
                            f.write(f"Video URL: {video_url}\n")
                            f.write(transcript + "\n\n")
                            self.status_update.emit(f"✓ Video {index}/{total_videos} done.")
                        else:
                            self.status_update.emit(f"✗ No transcript found for video {index}/{total_videos} — skipping.")
                    except Exception as e:
                        self.status_update.emit(f"✗ Error on video {index}/{total_videos}: {str(e)}")

                    self.progress_update.emit(int((index / total_videos) * 100))
                    delay = random.uniform(5.0, 8.0)
                    time.sleep(delay)

            self.extraction_complete.emit(self.output_file)
        except Exception as e:
            self.error_occurred.emit(f"Extraction error: {str(e)}")

    def _fetch_transcript(self, video_url, index, total, cookies):
        """Try yt-dlp first, fall back to youtube-transcript-api."""

        # --- Attempt 1: yt-dlp ---
        try:
            import yt_dlp
            self.status_update.emit(f"Fetching transcript for video {index}/{total} via yt-dlp...")
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": False,
                "writeautomaticsub": False,
                "subtitleslangs": ["en"],
                "quiet": True,
                "no_warnings": True,
            }
            if cookies:
                ydl_opts["cookiefile"] = cookies

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                # Try manual captions first, then automatic
                subtitles = info.get("subtitles", {})
                auto_subs = info.get("automatic_captions", {})
                chosen = subtitles.get("en") or auto_subs.get("en")
                if chosen:
                    # Get the plaintext format
                    for fmt in chosen:
                        if fmt.get("ext") == "json3" or fmt.get("ext") == "srv1":
                            import urllib.request
                            with urllib.request.urlopen(fmt["url"]) as r:
                                raw = r.read().decode("utf-8")
                            # Parse json3 format
                            if fmt.get("ext") == "json3":
                                import json as _json
                                data = _json.loads(raw)
                                words = []
                                for event in data.get("events", []):
                                    for seg in event.get("segs", []):
                                        words.append(seg.get("utf8", "").strip())
                                return " ".join(w for w in words if w and w != "\n")
                            else:
                                # srv1 is XML-like, strip tags
                                import re as _re
                                return " ".join(_re.sub(r"<[^>]+>", "", raw).split())
        except ImportError:
            self.status_update.emit("  yt-dlp not installed, falling back to youtube-transcript-api...")
        except Exception as e:
            self.status_update.emit(f"  yt-dlp failed ({str(e)[:60]}), trying fallback...")

        # --- Attempt 2: youtube-transcript-api with cookies + retry ---
        self.status_update.emit(f"Fetching transcript for video {index}/{total} via transcript API...")
        video_id = video_url.split("?v=")[1].split("&")[0]

        if cookies:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, cookies=cookies)
        else:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        try:
            t_obj = transcript_list.find_transcript(["en"])
        except NoTranscriptFound:
            self.status_update.emit(f"  English not found, using fallback language...")
            t_obj = next(iter(transcript_list))

        for attempt in range(1, 5):
            try:
                fetched = t_obj.fetch()
                return " ".join([seg['text'] for seg in fetched])
            except Exception as fetch_err:
                if "429" in str(fetch_err) and attempt < 4:
                    wait = 60 * attempt + random.randint(10, 30)
                    self.status_update.emit(f"  Rate limited. Waiting {wait}s (attempt {attempt}/3)...")
                    for _ in range(wait):
                        if not self._is_running:
                            return None
                        time.sleep(1)
                else:
                    raise
        return None

    def stop(self):
        self._is_running = False


class BookGenerationThread(QThread):
    progress_update = pyqtSignal(int)
    status_update = pyqtSignal(str)
    complete = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, transcript_file, output_file, api_key, model_name, language, book_title="Untitled Book"):
        super().__init__()
        self.transcript_file = transcript_file
        self.output_file = output_file
        self.api_key = api_key
        self.model_name = model_name
        self.language = language
        self.book_title = book_title
        self._is_running = True
        logging.basicConfig(
            filename="book_generation.log",
            level=logging.ERROR,
            format="%(asctime)s - %(levelname)s - %(message)s"
        )

    def run(self):
        try:
            client = Groq(api_key=self.api_key)

            video_sections = self._split_by_video(self.transcript_file)
            total = len(video_sections)

            if total == 0:
                self.error_occurred.emit("No content found in transcript file. Is the file empty?")
                return

            temp_file = self.output_file.replace(".txt", "_temp.txt")

            # Write book title header
            with open(self.output_file, "w", encoding="utf-8") as f:
                f.write(f"# {self.book_title}\n\n")
                f.write(f"*Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n---\n\n")

            for i, section in enumerate(video_sections, 1):
                if not self._is_running:
                    return

                self.status_update.emit(f"\nProcessing video {i}/{total}...")
                chunks = self._split_into_chunks(section, CHUNK_SIZE)
                previous_response = ""

                # Clear temp file for this video
                open(temp_file, "w", encoding="utf-8").close()

                for j, chunk in enumerate(chunks, 1):
                    if not self._is_running:
                        return

                    context = (
                        f"This is a continuation of the chapter. "
                        f"Previous output:\n{previous_response}\n\n"
                        f"Continue with the new text (do not repeat the previous output):\n"
                        if previous_response else ""
                    )
                    prompt = BOOK_PROMPT.replace("[Language]", self.language)
                    full_prompt = f"{context}{prompt}\n\n{chunk}"

                    self.status_update.emit(f"  Generating chapter part {j}/{len(chunks)}...")

                    try:
                        completion = client.chat.completions.create(
                            model=self.model_name,
                            messages=[{"role": "user", "content": full_prompt}],
                            max_tokens=4096,
                            temperature=0.7,
                        )
                        response_text = completion.choices[0].message.content
                    except Exception as e:
                        err = str(e)
                        if "rate_limit" in err.lower() or "429" in err:
                            self.status_update.emit(f"  Rate limited — waiting 60s...")
                            for _ in range(60):
                                if not self._is_running:
                                    return
                                time.sleep(1)
                            continue
                        self.error_occurred.emit(f"API error: {err}")
                        return

                    with open(temp_file, "a", encoding="utf-8") as f:
                        f.write(response_text + "\n\n")
                    previous_response = response_text

                # Append this video's chapter to the final output
                with open(temp_file, "r", encoding="utf-8") as f:
                    chapter_content = f.read()

                video_url = section.splitlines()[0].replace("Video URL: ", "").strip()
                with open(self.output_file, "a", encoding="utf-8") as f:
                    f.write(f"<!-- Source: {video_url} -->\n\n")
                    f.write(chapter_content)
                    f.write("\n\n---\n\n")

                self.status_update.emit(f"✓ Video {i}/{total} written to book.")
                self.progress_update.emit(int((i / total) * 100))

            # Clean up temp file
            if os.path.exists(temp_file):
                os.remove(temp_file)

            self.complete.emit(self.output_file)

        except Exception as e:
            msg = f"Book generation error: {str(e)}"
            self.error_occurred.emit(msg)
            logging.error(msg)

    def _split_by_video(self, file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        parts = re.split(r"(?=Video URL:)", content)
        sections = [p.strip() for p in parts if p.strip() and p.strip().startswith("Video URL:")]

        # Fallback: if no "Video URL:" markers found, treat entire file as one section
        if not sections and content.strip():
            self.status_update.emit("  No 'Video URL:' markers found — treating file as a single chapter.")
            sections = [f"Video URL: (plain transcript)\n{content.strip()}"]

        return sections

    def _split_into_chunks(self, text, chunk_size, min_chunk=500):
        words = text.split()
        chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]
        if len(chunks) > 1 and len(chunks[-1].split()) < min_chunk:
            chunks[-2] += " " + chunks[-1]
            chunks.pop()
        return chunks

    def stop(self):
        self._is_running = False


# ================================================================= main =====

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec_())