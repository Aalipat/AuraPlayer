using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace YouTubeDownloaderApp
{
    public class MiniPlayerForm : Form
    {
        [DllImport("user32.dll")]
        private static extern bool ReleaseCapture();
        [DllImport("user32.dll")]
        private static extern int SendMessage(IntPtr hWnd, int Msg, int wParam, int lParam);
        private const int WM_NCLBUTTONDOWN = 0xA1;
        private const int HT_CAPTION = 0x2;

        public event Action<string>? OnCommand;
        public event Action? OnRestoreMainWindow;

        private readonly PictureBox _coverPic;
        private readonly Label _titleLabel;
        private readonly Label _artistLabel;
        private readonly Button _prevBtn;
        private readonly Button _playBtn;
        private readonly Button _nextBtn;
        private readonly Button _closeBtn;
        private readonly Button _restoreBtn;

        private bool _isPlaying = false;

        public MiniPlayerForm()
        {
            FormBorderStyle = FormBorderStyle.None;
            TopMost = true;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            Size = new Size(420, 130);
            BackColor = Color.FromArgb(15, 23, 42); // slate-900
            DoubleBuffered = true;

            // Position at bottom-right corner of primary screen
            var workArea = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1920, 1080);
            Location = new Point(workArea.Right - 440, workArea.Bottom - 150);

            // Cover PictureBox (90x90)
            _coverPic = new PictureBox
            {
                Size = new Size(90, 90),
                Location = new Point(15, 20),
                SizeMode = PictureBoxSizeMode.Zoom,
                BackColor = Color.FromArgb(30, 41, 59),
                Cursor = Cursors.Hand
            };
            _coverPic.Click += (s, e) => OnRestoreMainWindow?.Invoke();
            Controls.Add(_coverPic);

            // Title Label
            _titleLabel = new Label
            {
                Location = new Point(115, 20),
                Size = new Size(240, 24),
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
                Location = new Point(115, 46),
                Size = new Size(240, 20),
                ForeColor = Color.FromArgb(148, 163, 184), // slate-400
                Font = new Font("Segoe UI", 9f, FontStyle.Regular),
                AutoEllipsis = true,
                Text = "YouTube & Spotify Downloader"
            };
            Controls.Add(_artistLabel);

            // Restore/Maximize Button
            _restoreBtn = new Button
            {
                Size = new Size(26, 26),
                Location = new Point(355, 12),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(148, 163, 184),
                Text = "⛶",
                Font = new Font("Segoe UI", 9f),
                Cursor = Cursors.Hand
            };
            _restoreBtn.FlatAppearance.BorderSize = 0;
            _restoreBtn.Click += (s, e) => OnRestoreMainWindow?.Invoke();
            Controls.Add(_restoreBtn);

            // Close Button
            _closeBtn = new Button
            {
                Size = new Size(26, 26),
                Location = new Point(385, 12),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(148, 163, 184),
                Text = "✕",
                Font = new Font("Segoe UI", 9.5f),
                Cursor = Cursors.Hand
            };
            _closeBtn.FlatAppearance.BorderSize = 0;
            _closeBtn.Click += (s, e) => Hide();
            Controls.Add(_closeBtn);

            // Prev Button (⏮)
            _prevBtn = new Button
            {
                Size = new Size(34, 34),
                Location = new Point(115, 76),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(203, 213, 225),
                Text = "⏮",
                Font = new Font("Segoe UI", 11f),
                Cursor = Cursors.Hand
            };
            _prevBtn.FlatAppearance.BorderSize = 0;
            _prevBtn.Click += (s, e) => OnCommand?.Invoke("prev");
            Controls.Add(_prevBtn);

            // Play/Pause Button (⏯)
            _playBtn = new Button
            {
                Size = new Size(38, 38),
                Location = new Point(160, 74),
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(16, 185, 129), // emerald-500
                ForeColor = Color.Black,
                Text = "▶",
                Font = new Font("Segoe UI", 12f, FontStyle.Bold),
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
                Size = new Size(34, 34),
                Location = new Point(210, 76),
                FlatStyle = FlatStyle.Flat,
                ForeColor = Color.FromArgb(203, 213, 225),
                Text = "⏭",
                Font = new Font("Segoe UI", 11f),
                Cursor = Cursors.Hand
            };
            _nextBtn.FlatAppearance.BorderSize = 0;
            _nextBtn.Click += (s, e) => OnCommand?.Invoke("next");
            Controls.Add(_nextBtn);

            // Allow dragging form by clicking on background or labels
            MouseDown += OnFormMouseDown;
            _titleLabel.MouseDown += OnFormMouseDown;
            _artistLabel.MouseDown += OnFormMouseDown;
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
            // Draw sleek subtle outer border
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
    }
}
