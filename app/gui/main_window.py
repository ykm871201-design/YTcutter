"""메인 창: 다운로드 → 미리보기 → 구간 지정 → 저장 흐름을 연결한다."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QShortcut
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QFrame, QScrollArea, QCheckBox, QComboBox, QDockWidget, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QRadioButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import APP_NAME, VERSION, ytdlp_update
from ..config import Settings
from ..cutter import Segment
from ..downloader import SSL_HINT, Downloader, find_deno, set_network_compat
from ..ffmpeg_tools import (OUTPUT_BY_KEY, OUTPUTS, FFmpegPaths, MediaInfo, NoAudioError,
                            audio_output_plan, find_ffmpeg)
from ..logging_setup import get_logger, log_file_path
from ..utils import (TimeFormatError, format_time, human_bytes, human_eta,
                     normalize_youtube_url, open_path, parse_time, validate_range)
from ..workers import CutWorker, DownloadWorker, InfoWorker, ProbeWorker, YtdlpUpdateWorker
from .dialogs import ask_ffmpeg, show_error
from .player import PlayerWidget
from .elide_label import ElideLabel
from .thumbnail import ThumbnailView

log = get_logger()

VIDEO_FILTER = "영상 파일 (*.mp4 *.mkv *.webm *.mov *.avi *.m4v *.ts *.flv);;모든 파일 (*.*)"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {VERSION} - 유튜브 다운로드 & 컷 편집")
        self.resize(1320, 820)
        self.settings = Settings.load()
        self.ff: FFmpegPaths | None = find_ffmpeg(self.settings.ffmpeg_path)
        set_network_compat(self.settings.network_compat)

        self.video_info = None               # downloader.VideoInfo
        self.current_file: str | None = None
        self.media: MediaInfo | None = None
        self.last_outputs: list[str] = []
        self.info_worker = self.dl_worker = self.cut_worker = self.probe_worker = None
        self.update_worker = None
        self.update_dlg: QProgressDialog | None = None
        self.net = QNetworkAccessManager(self)

        self._build_ui()
        self._fit_to_screen()
        self._build_menu()
        self._build_shortcuts()
        self.update_state()
        self.log_msg(f"{APP_NAME} {VERSION} 시작")
        if self.ff:
            self.log_msg(f"FFmpeg: {self.ff.ffmpeg}")
        else:
            self.log_msg("FFmpeg를 찾지 못했습니다. [설정 > FFmpeg 위치 지정]을 확인하세요.")
        deno, skipped = find_deno()
        for s in skipped:
            self.log_msg(f"경고: deno.exe 를 사용하지 않습니다 - {s}. "
                         "이 PC 에 맞는 Deno(보통 x86_64-pc-windows)로 바꿔 주세요.")
        self.log_msg(f"Deno: {deno}" if deno else "Deno 를 찾지 못했습니다 (일부 영상은 받지 못할 수 있음).")
        if ytdlp_update.active_version:
            self.log_msg(f"yt-dlp: 업데이트한 버전 {ytdlp_update.active_version} 사용")

    # ================================================================ UI 구성
    def _build_ui(self):
        # ---- 상단: URL
        self.ed_url = QLineEdit()
        self.ed_url.setPlaceholderText("유튜브 영상 주소를 붙여넣으세요 (예: https://www.youtube.com/watch?v=...)")
        self.ed_url.setClearButtonEnabled(True)
        self.btn_info = QPushButton("영상 정보 가져오기")
        self.btn_info.setDefault(True)
        self.btn_open_local = QPushButton("로컬 영상 열기...")
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("URL"))
        url_row.addWidget(self.ed_url, 1)
        url_row.addWidget(self.btn_info)
        url_row.addWidget(self.btn_open_local)

        # ---- 좌측: 영상 정보 / 화질 / 다운로드
        self.lbl_thumb = ThumbnailView()
        self.lbl_title = QLabel("-")
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setStyleSheet("font-weight:bold;")
        self.lbl_channel = QLabel("-")
        self.lbl_duration = QLabel("-")
        self.lbl_note = QLabel("")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setStyleSheet("color:#c77700;")
        self.cmb_quality = QComboBox()
        self.chk_h264 = QCheckBox("호환성 우선(H.264)")
        self.chk_h264.setChecked(self.settings.prefer_h264)
        self.chk_h264.setToolTip("H.264 + AAC (MP4) 포맷을 우선 선택합니다.\n"
                                 "유튜브의 H.264 포맷은 최대 1080p까지 제공됩니다.\n"
                                 "더 높은 화질은 VP9/AV1 코덱으로 받습니다.")
        self.btn_download = QPushButton("다운로드")
        self.btn_dl_cancel = QPushButton("취소")
        self.pb_dl = QProgressBar()
        self.pb_dl.setRange(0, 1000)
        self.pb_dl.setFormat("%p%")
        self.pb_dl.setMinimumWidth(120)
        self.lbl_dl_status = ElideLabel("대기 중", mode=Qt.ElideRight)
        self.cmb_quality.setMinimumWidth(110)
        # 다운로드 바: URL 바로 아래 한 줄 → 화면이 작아도 항상 보임
        dl_row = QHBoxLayout()
        dl_row.addWidget(QLabel("화질"))
        dl_row.addWidget(self.cmb_quality)
        dl_row.addWidget(self.chk_h264)
        dl_row.addWidget(self.btn_download)
        dl_row.addWidget(self.btn_dl_cancel)
        dl_row.addWidget(self.pb_dl, 1)
        dl_row.addWidget(self.lbl_dl_status, 2)
        top = QVBoxLayout()
        top.addLayout(url_row)
        top.addLayout(dl_row)

        info_box = QGroupBox("영상 정보")
        iv = QVBoxLayout(info_box)
        iv.addWidget(self.lbl_thumb, 0, Qt.AlignHCenter)   # 16:9 고정 크기, 가운데 정렬
        f = QFormLayout()
        iv.addLayout(f)
        f.addRow("제목", self.lbl_title)
        f.addRow("채널", self.lbl_channel)
        f.addRow("길이", self.lbl_duration)
        f.addRow(self.lbl_note)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(info_box)
        lv.addStretch(1)
        left.setMinimumWidth(300)
        left.setMaximumWidth(380)

        # ---- 중앙: 플레이어
        self.player = PlayerWidget()
        self.lbl_file = ElideLabel("열린 파일: 없음")
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self.lbl_file)
        cv.addWidget(self.player, 1)

        # ---- 우측: 저장 설정
        self.ed_outdir = QLineEdit(self.settings.save_dir)
        self.btn_outdir = QPushButton("변경...")
        self.btn_open_outdir = QPushButton("폴더 열기")
        self.rb_accurate = QRadioButton("정확한 컷 (권장)")
        self.rb_accurate.setToolTip("지정한 시점에 정확히 자릅니다. MP4(H.264+AAC)로 다시 인코딩하므로\n"
                                    "영상 길이에 따라 시간이 걸립니다.")
        self.rb_fast = QRadioButton("빠른 컷 (복사)")
        self.rb_fast.setToolTip("재인코딩 없이 복사해 매우 빠르지만, 키프레임 위치 때문에\n"
                                "실제 시작 지점이 지정한 시간보다 앞당겨질 수 있습니다.")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_accurate)
        self.mode_group.addButton(self.rb_fast)
        (self.rb_fast if self.settings.cut_mode == "fast" else self.rb_accurate).setChecked(True)
        self.lbl_mode_note = QLabel()
        self.lbl_mode_note.setWordWrap(True)
        self.cmb_output = QComboBox()
        for spec in OUTPUTS:
            self.cmb_output.addItem(spec.label, spec.key)
        idx = self.cmb_output.findData(self.settings.output)
        self.cmb_output.setCurrentIndex(idx if idx >= 0 else 0)
        self.lbl_output_note = QLabel()
        self.lbl_output_note.setWordWrap(True)
        self.lbl_output_note.setStyleSheet("color:#666;")
        self.btn_save_current = QPushButton("선택 구간 저장")
        self.btn_save_all = QPushButton("목록 전체 저장")
        self.btn_extract_all = QPushButton("영상 전체 음원 추출")
        self.btn_extract_all.setToolTip("구간과 상관없이 영상 전체의 소리만 뽑아 저장합니다.\n"
                                        "형식이 '영상'이면 MP3로 저장합니다.")
        self.btn_cut_cancel = QPushButton("저장 취소")
        self.pb_cut = QProgressBar()
        self.pb_cut.setRange(0, 1000)
        self.lbl_cut_status = ElideLabel("대기 중", mode=Qt.ElideRight)
        self.btn_open_result = QPushButton("결과 파일 열기")
        self.btn_show_result = QPushButton("결과 폴더 열기")

        out_box = QGroupBox("저장 폴더")
        ov = QVBoxLayout(out_box)
        ov.addWidget(self.ed_outdir)
        row = QHBoxLayout()
        row.addWidget(self.btn_outdir)
        row.addWidget(self.btn_open_outdir)
        ov.addLayout(row)
        mode_box = QGroupBox("저장 모드")
        mv = QVBoxLayout(mode_box)
        mv.addWidget(self.rb_accurate)
        mv.addWidget(self.rb_fast)
        mv.addWidget(self.lbl_mode_note)
        ex_box = QGroupBox("내보내기")
        ev = QVBoxLayout(ex_box)
        row = QHBoxLayout()
        row.addWidget(QLabel("형식"))
        row.addWidget(self.cmb_output, 1)
        ev.addLayout(row)
        ev.addWidget(self.lbl_output_note)
        ev.addWidget(self.btn_save_current)
        ev.addWidget(self.btn_save_all)
        ev.addWidget(self.btn_extract_all)
        ev.addWidget(self.btn_cut_cancel)
        ev.addWidget(self.pb_cut)
        ev.addWidget(self.lbl_cut_status)
        row = QHBoxLayout()
        row.addWidget(self.btn_open_result)
        row.addWidget(self.btn_show_result)
        ev.addLayout(row)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(ex_box)
        rv.addWidget(mode_box)
        rv.addWidget(out_box)
        rv.addStretch(1)
        right.setMinimumWidth(240)
        right.setMaximumWidth(330)

        # ---- 하단: 구간 지정
        self.ed_start = QLineEdit("00:00:00.000")
        self.ed_end = QLineEdit("00:00:00.000")
        for e in (self.ed_start, self.ed_end):
            e.setFixedWidth(110)
            e.setToolTip("HH:MM:SS.mmm 형식 (예: 00:01:23.500). MM:SS 도 입력 가능")
        self.btn_set_start = QPushButton("현재 위치로 (I)")
        self.btn_set_start.setToolTip("지금 재생 위치를 시작점으로 지정")
        self.btn_set_end = QPushButton("현재 위치로 (O)")
        self.btn_set_end.setToolTip("지금 재생 위치를 종료점으로 지정")
        self.btn_go_start = QPushButton("이동")
        self.btn_go_start.setToolTip("플레이어를 시작점으로 이동")
        self.btn_go_end = QPushButton("이동")
        self.btn_go_end.setToolTip("플레이어를 종료점으로 이동")
        self.lbl_len = QLabel("구간 길이: -")
        self.btn_preview = QPushButton("구간 미리보기 (P)")
        self.btn_add = QPushButton("구간 추가 (Enter)")
        self.ed_seg_name = QLineEdit()
        self.ed_seg_name.setPlaceholderText("구간 이름(선택, 파일명에 사용)")
        self.ed_seg_name.setMinimumWidth(150)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "시작", "종료", "길이", "이름"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setMinimumHeight(80)
        self.btn_seg_load = QPushButton("불러오기")
        self.btn_seg_load.setToolTip("선택한 구간을 시작/종료 칸에 불러옵니다 (더블클릭과 동일)")
        self.btn_seg_del = QPushButton("삭제")
        self.btn_seg_clear = QPushButton("모두 삭제")

        seg_box = QGroupBox("구간 지정")
        sv = QVBoxLayout(seg_box)
        grid = QGridLayout()
        grid.addWidget(QLabel("시작"), 0, 0)
        grid.addWidget(self.ed_start, 0, 1)
        grid.addWidget(self.btn_set_start, 0, 2)
        grid.addWidget(self.btn_go_start, 0, 3)
        grid.addWidget(QLabel("종료"), 1, 0)
        grid.addWidget(self.ed_end, 1, 1)
        grid.addWidget(self.btn_set_end, 1, 2)
        grid.addWidget(self.btn_go_end, 1, 3)
        grid.addWidget(self.lbl_len, 0, 4)
        grid.addWidget(self.btn_preview, 1, 4)
        grid.setColumnStretch(5, 1)
        grid.addWidget(self.ed_seg_name, 0, 6)
        grid.addWidget(self.btn_add, 1, 6)
        list_row = QHBoxLayout()
        list_row.addWidget(self.table, 1)
        side = QVBoxLayout()
        side.addWidget(self.btn_seg_load)
        side.addWidget(self.btn_seg_del)
        side.addWidget(self.btn_seg_clear)
        side.addStretch(1)
        list_row.addLayout(side)
        sv.addLayout(grid)
        sv.addLayout(list_row, 1)

        # ---- 배치
        mid = QSplitter(Qt.Horizontal)
        mid.addWidget(self._scrollable(left))
        mid.addWidget(center)
        mid.addWidget(self._scrollable(right))
        mid.setCollapsible(0, False)
        mid.setCollapsible(2, False)
        mid.setStretchFactor(1, 1)
        vsplit = self.vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(mid)
        vsplit.addWidget(seg_box)
        vsplit.setStretchFactor(0, 1)
        vsplit.setStretchFactor(1, 0)      # 창을 키우면 위쪽(영상)이 늘어나도록
        vsplit.setCollapsible(0, False)
        vsplit.setCollapsible(1, False)
        root = QWidget()
        rl = QVBoxLayout(root)
        rl.addLayout(top)
        rl.addWidget(vsplit, 1)
        self.setCentralWidget(root)

        # ---- 로그 패널
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_dock = QDockWidget("로그", self)
        self.log_dock.setWidget(self.log_view)
        self.log_dock.setObjectName("log")
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.setMinimumHeight(90)

        # ---- 시그널 연결
        self.btn_info.clicked.connect(self.on_fetch_info)
        self.ed_url.returnPressed.connect(self.on_fetch_info)
        self.btn_open_local.clicked.connect(self.on_open_local)
        self.btn_download.clicked.connect(self.on_download)
        self.btn_dl_cancel.clicked.connect(self.on_cancel_download)
        self.btn_outdir.clicked.connect(self.on_pick_outdir)
        self.btn_open_outdir.clicked.connect(self.on_open_outdir)
        self.rb_accurate.toggled.connect(self._update_mode_note)
        self.cmb_output.currentIndexChanged.connect(self.on_output_changed)
        self.btn_extract_all.clicked.connect(self.on_extract_all)
        self.btn_set_start.clicked.connect(lambda: self._set_from_player(self.ed_start))
        self.btn_set_end.clicked.connect(lambda: self._set_from_player(self.ed_end))
        self.btn_go_start.clicked.connect(lambda: self._seek_to_field(self.ed_start))
        self.btn_go_end.clicked.connect(lambda: self._seek_to_field(self.ed_end))
        self.ed_start.editingFinished.connect(lambda: self._normalize_field(self.ed_start))
        self.ed_end.editingFinished.connect(lambda: self._normalize_field(self.ed_end))
        self.ed_start.textChanged.connect(self._update_len)
        self.ed_end.textChanged.connect(self._update_len)
        self.btn_preview.clicked.connect(self.on_preview)
        self.btn_add.clicked.connect(self.on_add_segment)
        self.ed_seg_name.returnPressed.connect(self.on_add_segment)
        self.btn_seg_load.clicked.connect(self.on_load_segment)
        self.table.cellDoubleClicked.connect(lambda r, c: self.on_load_segment())
        self.table.itemSelectionChanged.connect(self.update_state)
        self.btn_seg_del.clicked.connect(self.on_delete_segment)
        self.btn_seg_clear.clicked.connect(self.on_clear_segments)
        self.btn_save_current.clicked.connect(lambda: self.on_export(all_segments=False))
        self.btn_save_all.clicked.connect(lambda: self.on_export(all_segments=True))
        self.btn_cut_cancel.clicked.connect(self.on_cancel_cut)
        self.btn_open_result.clicked.connect(self.on_open_result)
        self.btn_show_result.clicked.connect(self.on_show_result)
        self.player.errorOccurred.connect(lambda m: self.log_msg(f"재생 오류: {m}"))
        self.on_output_changed()

    @staticmethod
    def _scrollable(panel: QWidget) -> QScrollArea:
        """창 높이가 부족할 때 내용이 겹치지 않고 스크롤되도록 감싼다."""
        sa = QScrollArea()
        sa.setWidget(panel)
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sb = sa.verticalScrollBar().sizeHint().width()
        sa.setMinimumWidth(panel.minimumWidth() + sb)
        sa.setMaximumWidth(panel.maximumWidth() + sb)
        return sa

    def _fit_to_screen(self):
        """화면 배율(125%·150%)로 작업 영역이 작아도 창이 화면 안에 들어오게 한다."""
        scr = self.screen() or QApplication.primaryScreen()
        if not scr:
            return
        avail = scr.availableGeometry()
        w = min(1320, int(avail.width() * 0.95))
        h = min(820, int(avail.height() * 0.92))
        self.resize(w, h)
        self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)
        if avail.height() < 900:
            self.log_dock.hide()   # 화면이 작으면 로그 패널은 접어둠 (설정 메뉴에서 다시 표시)
        # 아래쪽 구간 지정 영역은 표 4~5줄 정도만 차지하게
        # 화면이 작으면 구간 영역을 최소 높이로, 크면 표를 조금 더 넉넉하게
        seg_min = self.vsplit.widget(1).minimumSizeHint().height()
        seg_h = seg_min + (60 if avail.height() >= 900 else 10)
        self.vsplit.setSizes([max(300, h - seg_h), seg_h])
        self.resizeDocks([self.log_dock], [100], Qt.Vertical)   # 로그는 3~4줄만

    def _build_menu(self):
        m_file = self.menuBar().addMenu("파일(&F)")
        a = QAction("로컬 영상 열기...", self, shortcut=QKeySequence.Open)
        a.triggered.connect(self.on_open_local)
        m_file.addAction(a)
        m_file.addSeparator()
        a = QAction("종료", self, shortcut=QKeySequence("Alt+F4"))
        a.triggered.connect(self.close)
        m_file.addAction(a)

        m_set = self.menuBar().addMenu("설정(&S)")
        a = QAction("FFmpeg 위치 지정...", self)
        a.triggered.connect(self.on_pick_ffmpeg)
        m_set.addAction(a)
        self.act_compat = QAction("네트워크 호환 모드 (학교·회사 보안망)", self, checkable=True)
        self.act_compat.setChecked(self.settings.network_compat)
        self.act_compat.setToolTip("SSL 인증서 오류(certificate verify failed)가 날 때 켜세요.")
        self.act_compat.toggled.connect(self.on_toggle_compat)
        m_set.addAction(self.act_compat)
        self.act_update = QAction("yt-dlp 업데이트...", self)
        self.act_update.setToolTip("다운로드가 안 되거나 403 오류가 날 때 최신 버전으로 업데이트하세요.")
        self.act_update.triggered.connect(self.on_update_ytdlp)
        m_set.addAction(self.act_update)
        m_set.addSeparator()
        a = QAction("로그 파일 열기", self)
        a.triggered.connect(lambda: open_path(log_file_path(), select=True))
        m_set.addAction(a)
        m_set.addAction(self.log_dock.toggleViewAction())

        m_help = self.menuBar().addMenu("도움말(&H)")
        a = QAction("사용 방법", self)
        a.triggered.connect(self.on_help)
        m_help.addAction(a)
        a = QAction("정보 / 라이선스", self)
        a.triggered.connect(self.on_about)
        m_help.addAction(a)

    def _build_shortcuts(self):
        # 텍스트 입력 중에는 동작하지 않도록 플레이어/표에만 단축키를 건다.
        def sc(key, fn):
            for w in (self.player, self.table):
                s = QShortcut(QKeySequence(key), w)
                s.setContext(Qt.WidgetWithChildrenShortcut)
                s.activated.connect(fn)
        sc("Space", self.player.toggle_play)
        sc("I", lambda: self._set_from_player(self.ed_start))
        sc("O", lambda: self._set_from_player(self.ed_end))
        sc("P", self.on_preview)
        sc("Left", lambda: self.player.step(-5))
        sc("Right", lambda: self.player.step(5))
        sc(",", lambda: self.player.step(-0.1))
        sc(".", lambda: self.player.step(0.1))
        sc("Return", self.on_add_segment)
        sc("Delete", self.on_delete_segment)
        self.player.setFocusPolicy(Qt.StrongFocus)
        self.player.video.setFocusPolicy(Qt.NoFocus)

    # ================================================================ 상태
    def busy(self) -> bool:
        return any(w is not None and w.isRunning()
                   for w in (self.info_worker, self.dl_worker, self.cut_worker, self.update_worker))

    def update_state(self):
        fetching = bool(self.info_worker and self.info_worker.isRunning())
        downloading = bool(self.dl_worker and self.dl_worker.isRunning())
        cutting = bool(self.cut_worker and self.cut_worker.isRunning())
        has_file = self.current_file is not None and self.media is not None
        self.btn_info.setEnabled(not (fetching or downloading))
        self.ed_url.setEnabled(not (fetching or downloading))
        self.btn_open_local.setEnabled(not (downloading or cutting))
        self.cmb_quality.setEnabled(self.video_info is not None and not downloading)
        self.chk_h264.setEnabled(not downloading)
        self.btn_download.setEnabled(self.video_info is not None and not (downloading or fetching))
        self.btn_dl_cancel.setEnabled(downloading)
        self.btn_outdir.setEnabled(not (downloading or cutting))
        self.ed_outdir.setEnabled(not (downloading or cutting))
        is_audio = OUTPUT_BY_KEY[self._output()].kind == "audio"
        self.rb_accurate.setEnabled(not cutting and not is_audio)
        self.rb_fast.setEnabled(not cutting and not is_audio)
        self.cmb_output.setEnabled(not cutting)
        can_edit = has_file and not cutting
        self.btn_extract_all.setEnabled(can_edit and self.media is not None
                                        and self.media.acodec is not None)
        for w in (self.btn_set_start, self.btn_set_end, self.btn_go_start, self.btn_go_end,
                  self.btn_preview, self.btn_add, self.ed_start, self.ed_end, self.ed_seg_name,
                  self.btn_save_current):
            w.setEnabled(can_edit)
        self.btn_save_all.setEnabled(can_edit and self.table.rowCount() > 0)
        sel = bool(self.table.selectionModel().selectedRows())
        self.btn_seg_load.setEnabled(can_edit and sel)
        self.btn_seg_del.setEnabled(not cutting and sel)
        self.btn_seg_clear.setEnabled(not cutting and self.table.rowCount() > 0)
        self.btn_cut_cancel.setEnabled(cutting)
        have_out = bool(self.last_outputs) and not cutting
        self.btn_open_result.setEnabled(have_out)
        self.btn_show_result.setEnabled(have_out)

    def log_msg(self, text: str):
        from datetime import datetime
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")
        log.info(text)

    def error(self, message: str, detail: str = ""):
        self.log_msg(f"오류: {message}")
        if detail:
            log.error(detail)
        show_error(self, message, detail)

    def ensure_ffmpeg(self) -> bool:
        if self.ff is None:
            self.ff = find_ffmpeg(self.settings.ffmpeg_path)
        if self.ff:
            return True
        if ask_ffmpeg(self):
            self.on_pick_ffmpeg()
        return self.ff is not None

    # ================================================================ 1) 정보 가져오기
    def on_fetch_info(self):
        if self.busy() and (self.info_worker and self.info_worker.isRunning()
                            or self.dl_worker and self.dl_worker.isRunning()):
            return
        url, err = normalize_youtube_url(self.ed_url.text())
        if err:
            self.error(err)
            return
        self.video_info = None
        self.cmb_quality.clear()
        self.lbl_title.setText("정보를 가져오는 중...")
        self.lbl_channel.setText("-")
        self.lbl_duration.setText("-")
        self.lbl_note.setText("")
        self.lbl_thumb.clear("썸네일 불러오는 중...")
        self.log_msg(f"영상 정보 요청: {url}")
        w = InfoWorker(url, self.ff.folder if self.ff else None, self)
        w.succeeded.connect(self._on_info_ok)
        w.failed.connect(self._on_info_fail)
        w.finished.connect(self.update_state)
        self.info_worker = w
        w.start()
        self.update_state()

    def _on_info_ok(self, info):
        self.video_info = info
        self.lbl_title.setText(info.title)
        self.lbl_channel.setText(info.channel)
        self.lbl_duration.setText(format_time(info.duration, with_ms=False) if info.duration else "-")
        self.lbl_note.setText(info.note)
        self.cmb_quality.clear()
        self.cmb_quality.addItem("최고 화질", None)
        for h in info.heights:
            self.cmb_quality.addItem(f"{h}p", h)
        # 기본값: 1080p 이하 중 가장 높은 화질
        for i in range(self.cmb_quality.count()):
            d = self.cmb_quality.itemData(i)
            if d and d <= 1080:
                self.cmb_quality.setCurrentIndex(i)
                break
        self.log_msg(f"정보 확인: {info.title} / {info.channel} / 화질 {info.heights}")
        if info.note:
            self.log_msg(info.note)
        if info.thumbnail:
            reply = self.net.get(QNetworkRequest(QUrl(info.thumbnail)))
            reply.finished.connect(lambda r=reply: self._on_thumb(r))
        else:
            self.lbl_thumb.clear("썸네일 없음")
        self.update_state()

    def _on_thumb(self, reply: QNetworkReply):
        if reply.error() == QNetworkReply.NoError:
            pm = QPixmap()
            if pm.loadFromData(reply.readAll().data()):
                self.lbl_thumb.setPixmap(pm)   # 크기 맞춤은 ThumbnailView 가 처리
            else:
                self.lbl_thumb.clear("썸네일 없음")
        else:
            self.lbl_thumb.clear("썸네일 없음")
        reply.deleteLater()

    def _on_info_fail(self, msg, detail):
        self.lbl_title.setText("-")
        self.lbl_thumb.clear("썸네일")
        if self._offer_compat(msg, detail):
            self.on_fetch_info()
            return
        self.error(msg, detail)

    # ================================================================ 2) 다운로드
    def on_download(self):
        if not self.video_info or (self.dl_worker and self.dl_worker.isRunning()):
            return
        if not self.ensure_ffmpeg():
            self.error("FFmpeg가 없어 영상·음성을 병합할 수 없습니다.")
            return
        out = self._out_dir()
        if not out:
            return
        height = self.cmb_quality.currentData()
        self.settings.prefer_h264 = self.chk_h264.isChecked()
        self.settings.save()
        dl = Downloader(self.video_info.webpage_url, out, height, self.chk_h264.isChecked(),
                        self.ff.folder, self.video_info.title)
        w = DownloadWorker(dl, self)
        w.progress.connect(self._on_dl_progress)
        w.message.connect(self.log_msg)
        w.succeeded.connect(self._on_dl_ok)
        w.failed.connect(self._on_dl_fail)
        w.cancelled.connect(self._on_dl_cancelled)
        w.finished.connect(self.update_state)
        self.dl_worker = w
        self.pb_dl.setValue(0)
        self.lbl_dl_status.setText("다운로드 준비 중...")
        self.log_msg(f"다운로드 시작 ({self.cmb_quality.currentText()}) → {out}")
        w.start()
        self.update_state()

    def _on_dl_progress(self, p):
        if p.phase == "merge":
            self.pb_dl.setRange(0, 0)   # 병합은 진행률을 알 수 없어 바쁨 표시
            self.lbl_dl_status.setText("영상·음성 병합 중...")
            return
        self.pb_dl.setRange(0, 1000)
        self.pb_dl.setValue(int(p.percent * 10))
        if p.phase == "download":
            speed = f"{human_bytes(p.speed)}/s" if p.speed else "-"
            size = f"{human_bytes(p.downloaded)} / {human_bytes(p.total)}"
            self.lbl_dl_status.setText(
                f"{p.label} · {size} · {speed} · 남은 시간 {human_eta(p.eta)}")

    def _on_dl_ok(self, path):
        self.pb_dl.setRange(0, 1000)
        self.pb_dl.setValue(1000)
        self.lbl_dl_status.setText(f"완료: {Path(path).name}")
        self.log_msg(f"다운로드 완료: {path}")
        self.load_file(path)

    def _on_dl_fail(self, msg, detail):
        self.pb_dl.setRange(0, 1000)
        self.pb_dl.setValue(0)
        self.lbl_dl_status.setText("실패")
        if self._offer_compat(msg, detail):
            self.on_download()
            return
        self.error(msg, detail)

    def _on_dl_cancelled(self):
        self.pb_dl.setRange(0, 1000)
        self.pb_dl.setValue(0)
        self.lbl_dl_status.setText("취소됨 (임시 파일 정리 완료)")
        self.log_msg("다운로드를 취소했습니다.")

    def on_cancel_download(self):
        if self.dl_worker and self.dl_worker.isRunning():
            self.dl_worker.cancel()
            self.lbl_dl_status.setText("취소하는 중... (병합 중이면 병합 후 정리)")
            self.btn_dl_cancel.setEnabled(False)

    # ================================================================ 3) 파일 열기/미리보기
    def on_open_local(self):
        start = str(Path(self.current_file).parent) if self.current_file else self.ed_outdir.text()
        path, _ = QFileDialog.getOpenFileName(self, "편집할 영상 열기", start, VIDEO_FILTER)
        if path:
            self.load_file(path)

    def load_file(self, path: str):
        if not self.ensure_ffmpeg():
            self.error("FFmpeg(ffprobe)가 없어 영상을 분석할 수 없습니다.")
            return
        self.log_msg(f"영상 분석 중: {path}")
        w = ProbeWorker(self.ff.ffprobe, path, self)
        w.succeeded.connect(self._on_probe_ok)
        w.failed.connect(self.error)
        self.probe_worker = w
        w.start()

    def _on_probe_ok(self, path: str, info: MediaInfo):
        if info.duration <= 0:
            self.error("영상 길이를 확인할 수 없습니다.", path)
            return
        if self.current_file != path and self.table.rowCount():
            self.table.setRowCount(0)
            self.log_msg("새 영상을 열어 구간 목록을 비웠습니다.")
        self.current_file, self.media = path, info
        self.player.load(path)
        self.player.set_duration_hint(info.duration)
        res = f"{info.width}x{info.height}" if info.width else "영상 없음"
        self.lbl_file.setText(f"열린 파일: {path}   ({res}, {info.vcodec or '-'}/{info.acodec or '-'}, "
                              f"{format_time(info.duration)})")
        self.ed_start.setText(format_time(0))
        self.ed_end.setText(format_time(info.duration))
        self.log_msg(f"영상 열기: {Path(path).name} (길이 {format_time(info.duration)})")
        if info.acodec is None:
            self.log_msg("안내: 이 영상에는 소리가 없어 음원을 추출할 수 없습니다.")
        self.on_output_changed()
        self.update_state()
        self.player.setFocus()

    # ================================================================ 4) 구간 지정
    def _set_from_player(self, field: QLineEdit):
        if not self.media:
            return
        field.setText(format_time(self.player.position()))

    def _seek_to_field(self, field: QLineEdit):
        try:
            self.player.seek(parse_time(field.text()))
        except TimeFormatError as e:
            self.error(str(e))

    def _normalize_field(self, field: QLineEdit):
        try:
            field.setText(format_time(parse_time(field.text())))
            field.setStyleSheet("")
        except TimeFormatError:
            field.setStyleSheet("border:1px solid #d33;")

    def _read_range(self, show=True) -> tuple[float, float] | None:
        try:
            s = parse_time(self.ed_start.text())
            e = parse_time(self.ed_end.text())
        except TimeFormatError as ex:
            if show:
                self.error(str(ex))
            return None
        msg = validate_range(s, e, self.media.duration if self.media else None)
        if msg:
            if show:
                self.error(msg)
            return None
        return s, e

    def _update_len(self):
        r = self._read_range(show=False)
        if r:
            self.lbl_len.setText(f"구간 길이: {format_time(r[1] - r[0])}")
            self.lbl_len.setStyleSheet("")
        else:
            self.lbl_len.setText("구간 길이: 잘못된 구간")
            self.lbl_len.setStyleSheet("color:#d33;")

    def on_preview(self):
        r = self._read_range()
        if r:
            self.player.play_range(*r)

    def on_add_segment(self):
        if not self.media:
            return
        r = self._read_range()
        if not r:
            return
        seg = Segment(r[0], r[1], self.ed_seg_name.text().strip())
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._set_row(row, seg)
        self.ed_seg_name.clear()
        self.log_msg(f"구간 추가: {format_time(seg.start)} ~ {format_time(seg.end)}")
        self.update_state()

    def _set_row(self, row: int, seg: Segment):
        vals = [str(row + 1), format_time(seg.start), format_time(seg.end),
                format_time(seg.length), seg.name]
        for c, v in enumerate(vals):
            it = QTableWidgetItem(v)
            if c == 0:
                it.setData(Qt.UserRole, seg)
            self.table.setItem(row, c, it)

    def _segments(self) -> list[Segment]:
        return [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())]

    def on_load_segment(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        seg: Segment = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        self.ed_start.setText(format_time(seg.start))
        self.ed_end.setText(format_time(seg.end))
        self.ed_seg_name.setText(seg.name)
        self.player.seek(seg.start)

    def on_delete_segment(self):
        rows = sorted((i.row() for i in self.table.selectionModel().selectedRows()), reverse=True)
        for r in rows:
            self.table.removeRow(r)
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setText(str(r + 1))
        self.update_state()

    def on_clear_segments(self):
        if self.table.rowCount() and QMessageBox.question(
                self, "확인", "구간 목록을 모두 삭제할까요?") == QMessageBox.Yes:
            self.table.setRowCount(0)
            self.update_state()

    # ================================================================ 5) 저장
    def _mode(self) -> str:
        return "fast" if self.rb_fast.isChecked() else "accurate"

    def _output(self) -> str:
        return self.cmb_output.currentData() or "video"

    def on_output_changed(self):
        """저장 형식(영상/음원)이 바뀌면 설명과 관련 버튼 상태를 갱신한다."""
        key = self._output()
        self.settings.output = key
        self.settings.save()
        spec = OUTPUT_BY_KEY[key]
        note = spec.note
        if spec.kind == "audio":
            ext_txt = {"mp3": "MP3", "m4a": "M4A", "wav": "WAV"}.get(spec.key, "원본 형식")
            if self.media and self.media.acodec is None:
                note = "⚠ 이 영상에는 소리가 없어 음원을 추출할 수 없습니다."
                self.lbl_output_note.setStyleSheet("color:#c77700;")
            else:
                self.lbl_output_note.setStyleSheet("color:#666;")
                if self.media:
                    try:
                        ext, _, warn = audio_output_plan(spec, self.media)
                        ext_txt = ext.upper()[1:]
                        note = warn if warn else f"{spec.note}\n저장 형식: {ext_txt}"
                    except NoAudioError:
                        pass
            self.btn_save_current.setText("선택 구간 음원 저장")
            self.btn_save_all.setText("목록 전체 음원 저장")
            self.btn_extract_all.setText(f"영상 전체 음원 추출 ({ext_txt})")
        else:
            self.lbl_output_note.setStyleSheet("color:#666;")
            self.btn_save_current.setText("선택 구간 저장")
            self.btn_save_all.setText("목록 전체 저장")
            self.btn_extract_all.setText("영상 전체 음원 추출 (MP3)")
        self.lbl_output_note.setText(note)
        # 저장 모드(정확한 컷/빠른 컷)는 영상 저장에만 적용된다
        for w in (self.rb_accurate, self.rb_fast):
            w.setEnabled(spec.kind == "video" and not (self.cut_worker and self.cut_worker.isRunning()))
        self._update_mode_note()

    def _update_mode_note(self):
        tip = ""
        if OUTPUT_BY_KEY[self._output()].kind == "audio":
            self.lbl_mode_note.setStyleSheet("color:#666;")
            self.lbl_mode_note.setToolTip("")
            self.lbl_mode_note.setText("음원 추출에는 적용되지 않습니다.")
            return
        if self.rb_fast.isChecked():
            text = "⚠ 키프레임 때문에 시작점이 앞당겨질 수 있음"
            tip = self.rb_fast.toolTip()
            if self.media:
                from ..ffmpeg_tools import fast_cut_container
                ext, note = fast_cut_container(self.media)
                text += f"\n출력 형식: {ext.upper()[1:]}"
                if note:
                    text += " (MP4 비호환 코덱)"
                    tip += "\n\n" + note
            self.lbl_mode_note.setStyleSheet("color:#c77700;")
        else:
            text = "지정한 시점에 정확히 · MP4로 재인코딩"
            tip = self.rb_accurate.toolTip()
            self.lbl_mode_note.setStyleSheet("color:#666;")
        self.lbl_mode_note.setToolTip(tip)
        self.lbl_mode_note.setText(text)

    def _out_dir(self) -> str | None:
        out = self.ed_outdir.text().strip()
        if not out:
            self.error("저장 폴더를 지정하세요.")
            return None
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.error("저장 폴더를 만들 수 없습니다. 다른 폴더를 선택하세요.", str(e))
            return None
        if not os.access(out, os.W_OK):
            self.error("저장 폴더에 쓸 권한이 없습니다. 다른 폴더를 선택하세요.")
            return None
        if out != self.settings.save_dir:
            self.settings.save_dir = out
            self.settings.save()
        return out

    def on_extract_all(self):
        """구간과 상관없이 영상 전체의 소리만 추출한다."""
        if not self.media or not self.current_file:
            return
        if OUTPUT_BY_KEY[self._output()].kind != "audio":
            self.cmb_output.setCurrentIndex(self.cmb_output.findData("mp3"))
        self.on_export(all_segments=False, whole=True)

    def on_export(self, all_segments: bool, whole: bool = False):
        if not self.media or not self.current_file:
            return
        if self.cut_worker and self.cut_worker.isRunning():
            return
        if not self.ensure_ffmpeg():
            return
        if whole:
            segs = [Segment(0.0, self.media.duration)]
        elif all_segments:
            segs = self._segments()
            if not segs:
                self.error("저장할 구간이 없습니다. [구간 추가]로 목록에 추가하세요.")
                return
        else:
            r = self._read_range()
            if not r:
                return
            segs = [Segment(r[0], r[1], self.ed_seg_name.text().strip())]
        out = self._out_dir()
        if not out:
            return
        if not Path(self.current_file).exists():
            self.error("원본 파일을 찾을 수 없습니다. 파일이 이동되거나 삭제되었는지 확인하세요.")
            return
        mode, output = self._mode(), self._output()
        spec = OUTPUT_BY_KEY[output]
        if spec.kind == "audio" and self.media.acodec is None:
            self.error("이 영상에는 소리가 없어 음원을 추출할 수 없습니다.\n"
                       "저장 형식을 '영상 (MP4)'으로 바꾸세요.")
            return
        self.settings.cut_mode = mode
        self.settings.save()
        w = CutWorker(self.ff, self.current_file, self.media, segs, out, mode,
                      self.settings.crf, self.settings.preset, output=output, parent=self)
        w.progress.connect(self._on_cut_progress)
        w.message.connect(self.log_msg)
        w.succeeded.connect(self._on_cut_ok)
        w.failed.connect(self._on_cut_fail)
        w.cancelled.connect(self._on_cut_cancelled)
        w.finished.connect(self.update_state)
        self.cut_worker = w
        self.pb_cut.setValue(0)
        if spec.kind == "audio":
            mode_txt = f"음원 추출({spec.label})"
        else:
            mode_txt = "정확한 컷" if mode == "accurate" else "빠른 컷"
        what = "영상 전체" if whole else f"{len(segs)}개 구간"
        self.lbl_cut_status.setText(f"{mode_txt} 준비 중...")
        self.log_msg(f"{mode_txt}로 {what} 저장 시작 → {out}")
        w.start()
        self.update_state()

    def _on_cut_progress(self, i, n, frac):
        self.pb_cut.setValue(int(((i - 1) + frac) / n * 1000))
        self.lbl_cut_status.setText(f"{i}/{n}번째 구간 저장 중... {frac * 100:.0f}%")

    def _on_cut_ok(self, files):
        self.last_outputs = files
        self.pb_cut.setValue(1000)
        self.lbl_cut_status.setText(f"완료: {len(files)}개 파일 저장")
        self.update_state()
        box = QMessageBox(self)
        box.setWindowTitle("저장 완료")
        box.setIcon(QMessageBox.Information)
        box.setText(f"{len(files)}개 파일을 저장했습니다.\n\n" +
                    "\n".join(Path(f).name for f in files[:10]) +
                    ("\n..." if len(files) > 10 else ""))
        b_file = box.addButton("결과 파일 열기", QMessageBox.ActionRole)
        b_dir = box.addButton("폴더 열기", QMessageBox.ActionRole)
        box.addButton("닫기", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_file:
            self.on_open_result()
        elif box.clickedButton() is b_dir:
            self.on_show_result()

    def _on_cut_fail(self, msg, detail):
        self.pb_cut.setValue(0)
        self.lbl_cut_status.setText("실패")
        self.error(msg, detail)

    def _on_cut_cancelled(self):
        self.pb_cut.setValue(0)
        self.lbl_cut_status.setText("취소됨 (작성 중이던 파일 삭제)")
        self.log_msg("구간 저장을 취소했습니다.")

    def on_cancel_cut(self):
        if self.cut_worker and self.cut_worker.isRunning():
            self.cut_worker.cancel()
            self.btn_cut_cancel.setEnabled(False)

    # ================================================================ 기타
    def on_toggle_compat(self, on: bool):
        self.settings.network_compat = on
        self.settings.save()
        set_network_compat(on)
        self.log_msg(f"네트워크 호환 모드: {'켜짐' if on else '꺼짐'}")

    def _offer_compat(self, msg: str, detail: str) -> bool:
        """SSL 인증서 오류이면 호환 모드를 켜고 다시 시도할지 묻는다. 다시 시도하면 True."""
        if msg != SSL_HINT or self.settings.network_compat:
            return False
        self.log_msg("SSL 인증서 오류: " + (detail.strip().splitlines() or [""])[-1][:200])
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("보안 연결 오류")
        box.setText("보안 연결(SSL 인증서) 확인에 실패했습니다.\n\n"
                    "학교·회사 네트워크의 보안 장비 때문에 자주 생기는 문제입니다.\n"
                    "'네트워크 호환 모드'를 켜고 다시 시도할까요?\n\n"
                    "(Windows 인증서 저장소를 사용하고, 보안 장비의 약한 인증서 키를 허용합니다. "
                    "인증서 검증 자체는 유지됩니다.)")
        box.setDetailedText(detail)
        yes = box.addButton("켜고 다시 시도", QMessageBox.AcceptRole)
        box.addButton("취소", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is yes:
            self.act_compat.setChecked(True)   # on_toggle_compat 호출됨
            return True
        return False

    def on_update_ytdlp(self):
        if self.busy():
            QMessageBox.information(self, "yt-dlp 업데이트", "진행 중인 작업이 끝난 뒤에 업데이트하세요.")
            return
        self.log_msg("yt-dlp 업데이트 시작")
        dlg = QProgressDialog("yt-dlp 최신 버전을 확인하는 중...", None, 0, 0, self)
        dlg.setWindowTitle("yt-dlp 업데이트")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setMinimumWidth(360)
        dlg.show()
        self.update_dlg = dlg
        w = YtdlpUpdateWorker(self.settings.network_compat, self)
        w.message.connect(self.log_msg)
        w.message.connect(dlg.setLabelText)
        w.succeeded.connect(self._on_update_ok)
        w.failed.connect(self._on_update_fail)
        self.update_worker = w
        self.act_update.setEnabled(False)
        w.start()

    def _close_update_dlg(self):
        if self.update_dlg:
            self.update_dlg.close()
            self.update_dlg = None
        self.act_update.setEnabled(True)

    def _on_update_ok(self, res):
        self._close_update_dlg()
        if not res.updated:
            self.log_msg(f"yt-dlp 는 이미 최신 버전입니다 ({res.after})")
            QMessageBox.information(self, "yt-dlp 업데이트", f"이미 최신 버전입니다.\n\n현재 버전: {res.after}")
            return
        self.log_msg(f"yt-dlp 업데이트 완료: {res.before} → {res.after}")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("yt-dlp 업데이트")
        box.setText(f"yt-dlp 를 업데이트했습니다.\n\n{res.before} → {res.after}\n\n"
                    "프로그램을 다시 시작해야 새 버전이 적용됩니다.")
        restart = box.addButton("지금 다시 시작", QMessageBox.AcceptRole)
        box.addButton("나중에", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is restart:
            self._restart()

    def _on_update_fail(self, msg, detail):
        self._close_update_dlg()
        if self._offer_compat(msg, detail):
            self.update_worker.wait()
            self.on_update_ytdlp()
            return
        self.error(msg, detail)

    def _restart(self):
        """프로그램을 다시 실행한다 (업데이트한 yt-dlp 적용)."""
        if getattr(sys, "frozen", False):
            args = sys.argv[1:]
        else:
            args = [str(Path(sys.argv[0]).resolve()), *sys.argv[1:]]
        if self.close():
            QProcess.startDetached(sys.executable, args)

    def on_pick_outdir(self):
        d = QFileDialog.getExistingDirectory(self, "저장 폴더 선택", self.ed_outdir.text())
        if d:
            self.ed_outdir.setText(str(Path(d)))
            self.settings.save_dir = str(Path(d))
            self.settings.save()

    def on_open_outdir(self):
        out = self._out_dir()
        if out:
            open_path(out)

    def on_open_result(self):
        if self.last_outputs and Path(self.last_outputs[0]).exists():
            open_path(self.last_outputs[0])

    def on_show_result(self):
        if self.last_outputs:
            open_path(self.last_outputs[0], select=True)

    def on_pick_ffmpeg(self):
        exe = "ffmpeg.exe (ffmpeg.exe)" if os.name == "nt" else "ffmpeg (ffmpeg)"
        path, _ = QFileDialog.getOpenFileName(self, "ffmpeg 실행 파일 선택", "", f"{exe};;모든 파일 (*)")
        if not path:
            return
        ff = find_ffmpeg(path)
        if ff and Path(ff.ffmpeg).parent == Path(path).parent:
            self.ff = ff
            self.settings.ffmpeg_path = path
            self.settings.save()
            self.log_msg(f"FFmpeg 설정: {ff.ffmpeg}")
            QMessageBox.information(self, "FFmpeg", "FFmpeg 위치를 저장했습니다.")
        else:
            self.error("선택한 폴더에서 ffmpeg와 ffprobe를 함께 찾지 못했습니다.\n"
                       "두 파일이 같은 폴더(bin)에 있어야 합니다.")

    def on_help(self):
        QMessageBox.information(self, "사용 방법", (
            "1. 유튜브 주소를 붙여넣고 [영상 정보 가져오기]\n"
            "2. 화질과 저장 폴더를 고르고 [다운로드]\n"
            "3. 다운로드가 끝나면 영상이 자동으로 열립니다 (또는 [로컬 영상 열기])\n"
            "4. 재생하며 [현재 위치 → 시작/종료]로 구간 지정 (시간 직접 입력도 가능)\n"
            "5. [구간 미리보기]로 확인 → [선택 구간 저장] 또는 [구간 추가] 후 [목록 전체 저장]\n\n"
            "■ 음원만 뽑고 싶을 때\n"
            "· 영상 전체: [영상 전체 음원 추출] 버튼 한 번이면 끝 (구간 지정 필요 없음)\n"
            "· 일부 구간만: [형식]을 '음원 MP3' 등으로 바꾼 뒤 위와 똑같이 구간 저장\n"
            "· 형식 고르기: MP3(어디서나 재생) · M4A(같은 용량에 더 좋은 음질) ·\n"
            "  WAV(무손실, 용량 큼) · 원본 그대로(변환 없이 가장 빠름)\n\n"
            "단축키 (영상 화면을 한 번 클릭한 뒤):\n"
            "  Space 재생/일시정지 · I 시작점 · O 종료점 · P 구간 미리보기\n"
            "  ←/→ 5초 이동 · , / . 0.1초 이동 · Enter 구간 추가 · Delete 구간 삭제\n\n"
            "원본 파일은 수정되지 않으며, 같은 이름의 파일이 있으면 '이름 (1)' 처럼 새 이름으로 저장합니다."))

    def on_about(self):
        QMessageBox.about(self, "정보", (
            f"<b>{APP_NAME} {VERSION}</b><br>유튜브 영상 다운로드 & 구간 컷 편집기<br><br>"
            "사용 구성요소: yt-dlp (Unlicense), FFmpeg (LGPL/GPL), "
            "Qt for Python / PySide6 (LGPLv3)<br><br>"
            "본인이 소유했거나 다운로드·편집 권한이 있는 영상에만 사용하세요. "
            "YouTube 서비스 약관과 저작권법을 준수할 책임은 사용자에게 있습니다. "
            "DRM 등 보호 조치를 우회하는 기능은 포함되어 있지 않습니다."))

    def closeEvent(self, ev):
        running = [w for w in (self.dl_worker, self.cut_worker, self.info_worker, self.probe_worker,
                               self.update_worker)
                   if w is not None and w.isRunning()]
        if running:
            if QMessageBox.question(self, "종료", "작업이 진행 중입니다. 취소하고 종료할까요?") \
                    != QMessageBox.Yes:
                ev.ignore()
                return
            for w in running:
                if hasattr(w, "cancel"):
                    w.cancel()
            for w in running:
                w.wait(15000)
        self.player.unload()
        self.settings.save_dir = self.ed_outdir.text().strip() or self.settings.save_dir
        self.settings.save()
        ev.accept()
