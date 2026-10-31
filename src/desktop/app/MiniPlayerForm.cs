using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace YouTubeDownloaderApp
{
    public class MiniPlayerForm : Form
    {
        #region Win32 Native APIs & Constants
        [DllImport("user32.dll")]
        private static extern bool ReleaseCapture();
        [DllImport("user32.dll")]
        private static extern int SendMessage(IntPtr hWnd, int Msg, int wParam, int lParam);

        private const int WM_NCLBUTTONDOWN = 0xA1;
        private const int HT_CAPTION = 0x2;

        private const int WM_NCHITTEST = 0x84;
        private const int HTCLIENT = 1;
        private const int HTLEFT = 10;
        private const int HTRIGHT = 11;
        private const int HTTOP = 12;
        private const int HTTOPLEFT = 13;
        private const int HTTOPRIGHT = 14;
        private const int HTBOTTOM = 15;
        private const int HTBOTTOMLEFT = 16;
        private const int HTBOTTOMRIGHT = 17;
        private const int RESIZE_BORDER = 8;
        #endregion

        public event Action<string>? OnCommand;
        public event Action? OnRestoreMainWindow;

        private readonly PictureBox _coverPic;
        private readonly Label _titleLabel;
        private readonly Label _artistLabel;
        private readonly Label _timeLabel;
        private readonly Panel _progressTrack;
        private readonly Panel _progressBar;
        private readonly Button _prevBtn;
        private readonly Button _playBtn;
        private readonly Button _nextBtn;
        private readonly Button _volBtn;
        private readonly Button _pinBtn;
        private readonly Button _restoreBtn;
        private readonly Button _closeBtn;

        private bool _isPlaying = false;
        private bool _isPinned = true;
        private bool _isMuted = false;
        private double _currentSeconds = 0;
        private double _totalSeconds = 0;

        public MiniPlayerForm()
        {
            FormBorderStyle = FormBorderStyle.None;
            TopMost = true;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            Size = new Size(440, 136);
            MinimumSize = new Size(360, 115);
            MaximumSize = new Size(820, 320);
            BackColor = Color.FromArgb(15, 23, 42); // slate-900
            DoubleBuffered = true;

            // Position at bottom-right corner of primary screen
            var workArea = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1920, 1080);
            Location = new Point(workArea.Right - 460, workArea.Bottom - 160);

            // Cover PictureBox (dynamic zoom)
            _coverPic = new PictureBox
            {
                Size = new Size(84, 84),
                Location = new Point(16, 16),
                SizeMode = PictureBoxSizeMode.Zoom,
                BackColor = Color.FromArgb(30, 41, 59),
                Cursor = Cursors.Hand
            };
            _coverPic.Click += (s, e) => OnRestoreMainWindow?.Invoke();
            Controls.Add(_coverPic);

            // Title Label
            _titleLabel = new Label
            {
                Location = new Point(112, 14),
                Size = new Size(220, 22),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 10.5f, FontStyle.Bold),
                AutoEllipsis = true,
                Text = "آماده پخش...",
                Cursor = Cursors.Hand
            };
            _titleLabel.Click += (s, e) => OnRestoreMainWindow?.Invoke();
            Controls.Add(_titleLabel);

            // Artist Label
            _artistLabel = new Label
            {
                Location = new Point(112, 38),
                Size = new Size(220, 18),
                ForeColor = Color.FromArgb(148, 163, 184), // slate-400
                Font = new Font("Segoe UI", 8.5f, FontStyle.Regular),
                AutoEllipsis = true,
                Text = "YouTube & Spotify Downloader"
            };
            Controls.Add(_artistLabel);

            // Progress Track Container
            _progressTrack = new Panel
            {
                Location = new Point(112, 62),
                Size = new Size(220, 5),
                BackColor = Color.FromArgb(51, 65, 85), // slate-700
                Cursor = Cursors.Hand
            };
            _progressTrack.MouseClick += OnProgressTrackClick;

            _progressBar = new Panel
            {
                Location = new Point(0, 0),
                Size = new Size(0, 5),
                BackColor = Color.FromArgb(16, 185, 129), // emerald-500
                Cursor = Cursors.Hand
            };
            _progressBar.MouseClick += OnProgressTrackClick;
            _progressTrack.Controls.Add(_progressBar);
            Controls.Add(_progressTrack);

            // Time Label (00:00 / 00:00)
            _timeLabel = new Label
            {
                Location = new Point(112, 70),
                Size = new Size(110, 16),
                ForeColor = Color.FromArgb(100, 116, 139), // slate-500
                Font = new Font("Segoe UI", 7.5f, FontStyle.Bold),
                Text = "00:00 / 00:00"
            };
            Controls.Add(_timeLabel);

            // Pin / Unpin Button (📌)
            _pinBtn = new Button
            {
                Size = new Size(24, 24),
                Location = new Point(340, 10),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(16, 185, 129),
                Text = "📌",
                Font = new Font("Segoe UI", 8f),
                Cursor = Cursors.Hand
            };
            _pinBtn.FlatAppearance.BorderSize = 0;
            _pinBtn.Click += (s, e) => TogglePin();
            Controls.Add(_pinBtn);

            // Restore / Expand Button (⛶)
            _restoreBtn = new Button
            {
                Size = new Size(24, 24),
                Location = new Point(370, 10),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(148, 163, 184),
                Text = "⛶",
                Font = new Font("Segoe UI", 9f),
                Cursor = Cursors.Hand
            };
            _restoreBtn.FlatAppearance.BorderSize = 0;
            _restoreBtn.Click += (s, e) => OnRestoreMainWindow?.Invoke();
            Controls.Add(_restoreBtn);

            // Close Button (✕)
            _closeBtn = new Button
            {
                Size = new Size(24, 24),
                Location = new Point(400, 10),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(148, 163, 184),
                Text = "✕",
                Font = new Font("Segoe UI", 9f),
                Cursor = Cursors.Hand
            };
            _closeBtn.FlatAppearance.BorderSize = 0;
            _closeBtn.Click += (s, e) => Hide();
            Controls.Add(_closeBtn);

            // Prev Button (⏮)
            _prevBtn = new Button
            {
                Size = new Size(32, 32),
                Location = new Point(112, 92),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(203, 213, 225),
                Text = "⏮",
                Font = new Font("Segoe UI", 10.5f),
                Cursor = Cursors.Hand
            };
            _prevBtn.FlatAppearance.BorderSize = 0;
            _prevBtn.Click += (s, e) => OnCommand?.Invoke("prev");
            Controls.Add(_prevBtn);

            // Play/Pause Button (⏯)
            _playBtn = new Button
            {
                Size = new Size(36, 36),
                Location = new Point(152, 90),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(16, 185, 129), // emerald-500
                ForeColor = Color.Black,
                Text = "▶",
                Font = new Font("Segoe UI", 11.5f, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            _playBtn.FlatAppearance.BorderSize = 0;
            _playBtn.Click += (s, e) =>
            {
                if (_isPlaying)
                    OnCommand?.Invoke("pause");
                else
                    OnCommand?.Invoke("play");
            };
            Controls.Add(_playBtn);

            // Next Button (⏭)
            _nextBtn = new Button
            {
                Size = new Size(32, 32),
                Location = new Point(196, 92),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(203, 213, 225),
                Text = "⏭",
                Font = new Font("Segoe UI", 10.5f),
                Cursor = Cursors.Hand
            };
            _nextBtn.FlatAppearance.BorderSize = 0;
            _nextBtn.Click += (s, e) => OnCommand?.Invoke("next");
            Controls.Add(_nextBtn);

            // Volume Toggle Button (🔊)
            _volBtn = new Button
            {
                Size = new Size(30, 30),
                Location = new Point(240, 93),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(148, 163, 184),
                Text = "🔊",
                Font = new Font("Segoe UI", 9.5f),
                Cursor = Cursors.Hand
            };
            _volBtn.FlatAppearance.BorderSize = 0;
            _volBtn.Click += (s, e) => OnCommand?.Invoke("mute");
            Controls.Add(_volBtn);

            // Drag window on background and labels
            MouseDown += OnFormMouseDown;
            _titleLabel.MouseDown += OnFormMouseDown;
            _artistLabel.MouseDown += OnFormMouseDown;
            _timeLabel.MouseDown += OnFormMouseDown;
        }

        private void TogglePin()
        {
            _isPinned = !_isPinned;
            TopMost = _isPinned;
            _pinBtn.ForeColor = _isPinned ? Color.FromArgb(16, 185, 129) : Color.FromArgb(100, 116, 139);
        }

        private void OnProgressTrackClick(object? sender, MouseEventArgs e)
        {
            if (_totalSeconds <= 0) return;
            Point p = _progressTrack.PointToClient(Cursor.Position);
            double ratio = Math.Max(0.0, Math.Min(1.0, (double)p.X / _progressTrack.Width));
            double seekTime = ratio * _totalSeconds;
            OnCommand?.Invoke($"seek:{seekTime:F2}");
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);

            int rightEdge = ClientSize.Width;
            int bottomEdge = ClientSize.Height;

            // Top action buttons anchored top-right
            if (_closeBtn != null) _closeBtn.Location = new Point(rightEdge - 32, 10);
            if (_restoreBtn != null) _restoreBtn.Location = new Point(rightEdge - 60, 10);
            if (_pinBtn != null) _pinBtn.Location = new Point(rightEdge - 88, 10);

            // Title & Artist width expands with window
            int textWidth = Math.Max(120, rightEdge - 115 - 95);
            if (_titleLabel != null) _titleLabel.Width = textWidth;
            if (_artistLabel != null) _artistLabel.Width = textWidth;

            // Progress track expands across window width
            int progressWidth = Math.Max(120, rightEdge - 115 - 20);
            if (_progressTrack != null)
            {
                _progressTrack.Width = progressWidth;
                UpdateProgressBarWidth();
            }

            // Reposition playback controls anchored bottom
            int controlsY = Math.Max(70, bottomEdge - 44);
            if (_prevBtn != null) _prevBtn.Location = new Point(112, controlsY);
            if (_playBtn != null) _playBtn.Location = new Point(152, controlsY - 2);
            if (_nextBtn != null) _nextBtn.Location = new Point(196, controlsY);
            if (_volBtn != null) _volBtn.Location = new Point(236, controlsY);

            // Time label right-aligned on bottom
            if (_timeLabel != null)
            {
                _timeLabel.Location = new Point(Math.Max(280, rightEdge - 120), controlsY + 8);
            }

            Invalidate();
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == WM_NCHITTEST)
            {
                base.WndProc(ref m);
                Point pt = PointToClient(new Point(m.LParam.ToInt32() & 0xFFFF, (m.LParam.ToInt32() >> 16) & 0xFFFF));

                bool left = pt.X <= RESIZE_BORDER;
                bool right = pt.X >= ClientSize.Width - RESIZE_BORDER;
                bool top = pt.Y <= RESIZE_BORDER;
                bool bottom = pt.Y >= ClientSize.Height - RESIZE_BORDER;

                if (left && top) { m.Result = (IntPtr)HTTOPLEFT; return; }
                if (right && top) { m.Result = (IntPtr)HTTOPRIGHT; return; }
                if (left && bottom) { m.Result = (IntPtr)HTBOTTOMLEFT; return; }
                if (right && bottom) { m.Result = (IntPtr)HTBOTTOMRIGHT; return; }
                if (left) { m.Result = (IntPtr)HTLEFT; return; }
                if (right) { m.Result = (IntPtr)HTRIGHT; return; }
                if (top) { m.Result = (IntPtr)HTTOP; return; }
                if (bottom) { m.Result = (IntPtr)HTBOTTOM; return; }
                return;
            }

            base.WndProc(ref m);
        }

        private void OnFormMouseDown(object? sender, MouseEventArgs e)
        {
            if (e.Button == MouseButtons.Left)
            {
                ReleaseCapture();
                SendMessage(Handle, WM_NCLBUTTONDOWN, HT_CAPTION, 0);
            }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            using var pen = new Pen(Color.FromArgb(51, 65, 85), 1.5f); // slate-700
            e.Graphics.DrawRectangle(pen, 0, 0, Width - 1, Height - 1);
        }

        public void UpdateInfo(string title, string artist, bool isPlaying, string coverUrl = "")
        {
            if (InvokeRequired)
            {
                Invoke(() => UpdateInfo(title, artist, isPlaying, coverUrl));
                return;
            }

            _titleLabel.Text = string.IsNullOrWhiteSpace(title) ? "نام قطعه" : title;
            _artistLabel.Text = string.IsNullOrWhiteSpace(artist) ? "هنرمند" : artist;
            UpdatePlayState(isPlaying);

            if (!string.IsNullOrEmpty(coverUrl))
            {
                try
                {
                    _coverPic.LoadAsync(coverUrl);
                }
                catch
                {
                    _coverPic.Image = null;
                }
            }
            else
            {
                _coverPic.Image = null;
            }
        }

        public void UpdatePlayState(bool isPlaying)
        {
            if (InvokeRequired)
            {
                Invoke(() => UpdatePlayState(isPlaying));
                return;
            }

            _isPlaying = isPlaying;
            _playBtn.Text = isPlaying ? "⏸" : "▶";
            _playBtn.BackColor = isPlaying ? Color.FromArgb(16, 185, 129) : Color.FromArgb(34, 197, 94);
        }

        public void UpdateProgress(double currentSec, double totalSec)
        {
            if (InvokeRequired)
            {
                Invoke(() => UpdateProgress(currentSec, totalSec));
                return;
            }

            _currentSeconds = Math.Max(0, currentSec);
            _totalSeconds = Math.Max(0, totalSec);

            string curStr = FormatTime((int)_currentSeconds);
            string totStr = FormatTime((int)_totalSeconds);
            _timeLabel.Text = $"{curStr} / {totStr}";

            UpdateProgressBarWidth();
        }

        private void UpdateProgressBarWidth()
        {
            if (_totalSeconds <= 0 || _progressTrack == null || _progressBar == null) return;
            double pct = Math.Min(1.0, _currentSeconds / _totalSeconds);
            int barWidth = (int)(pct * _progressTrack.Width);
            _progressBar.Width = Math.Max(0, barWidth);
        }

        public void UpdateVolumeState(bool isMuted)
        {
            if (InvokeRequired)
            {
                Invoke(() => UpdateVolumeState(isMuted));
                return;
            }

            _isMuted = isMuted;
            _volBtn.Text = isMuted ? "🔇" : "🔊";
            _volBtn.ForeColor = isMuted ? Color.FromArgb(244, 63, 94) : Color.FromArgb(148, 163, 184);
        }

        private static string FormatTime(int seconds)
        {
            int m = seconds / 60;
            int s = seconds % 60;
            return $"{m:D2}:{s:D2}";
        }
    }
}
