using System;
using System.ComponentModel;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace YouTubeDownloaderApp
{
    #region Custom Sleek Controls for MiniPlayer
    public class RoundedPictureBox : PictureBox
    {
        [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public int CornerRadius { get; set; } = 10;
        public RoundedPictureBox()
        {
            DoubleBuffered = true;
            SizeMode = PictureBoxSizeMode.Zoom;
            BackColor = Color.Transparent;
        }

        protected override void OnPaint(PaintEventArgs pe)
        {
            var g = pe.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var bounds = new Rectangle(0, 0, Width, Height);
            using var path = MiniPlayerForm.CreateRoundRectPath(bounds, CornerRadius);

            g.SetClip(path);
            if (Image != null)
            {
                g.DrawImage(Image, bounds);
            }
            else
            {
                using var b = new SolidBrush(Color.FromArgb(30, 41, 59));
                g.FillRectangle(b, bounds);
            }
            g.ResetClip();

            using var pen = new Pen(Color.FromArgb(50, 255, 255, 255), 1.2f);
            g.DrawPath(pen, path);
        }
    }

    public class CircularIconButton : Button
    {
        [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public bool IsPlayButton { get; set; } = false;

        [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public bool IsPlaying { get; set; } = false;

        [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public string IconType { get; set; } = "play"; // "play", "prev", "next", "vol", "pin", "restore", "close"

        [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public Color AccentColor { get; set; } = Color.FromArgb(16, 185, 129);

        [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public bool IsPinned { get; set; } = true;

        [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public bool IsMuted { get; set; } = false;

        private bool _isHovered = false;
        private bool _isPressed = false;

        public CircularIconButton()
        {
            FlatStyle = FlatStyle.Flat;
            FlatAppearance.BorderSize = 0;
            BackColor = Color.Transparent;
            DoubleBuffered = true;
            Cursor = Cursors.Hand;
        }

        protected override void OnMouseEnter(EventArgs e) { base.OnMouseEnter(e); _isHovered = true; Invalidate(); }
        protected override void OnMouseLeave(EventArgs e) { base.OnMouseLeave(e); _isHovered = false; _isPressed = false; Invalidate(); }
        protected override void OnMouseDown(MouseEventArgs mevent) { base.OnMouseDown(mevent); _isPressed = true; Invalidate(); }
        protected override void OnMouseUp(MouseEventArgs mevent) { base.OnMouseUp(mevent); _isPressed = false; Invalidate(); }

        protected override void OnPaint(PaintEventArgs pe)
        {
            var g = pe.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = new Rectangle(1, 1, Width - 2, Height - 2);

            if (IsPlayButton)
            {
                // Play button with vibrant accent circle
                Color bg = _isPressed
                    ? ControlPaint.Dark(AccentColor, 0.15f)
                    : (_isHovered ? ControlPaint.Light(AccentColor, 0.15f) : AccentColor);

                using var brush = new SolidBrush(bg);
                g.FillEllipse(brush, rect);

                using var ringPen = new Pen(Color.FromArgb(60, 255, 255, 255), 1.2f);
                g.DrawEllipse(ringPen, rect);

                int cx = Width / 2;
                int cy = Height / 2;
                using var iconBrush = new SolidBrush(Color.FromArgb(15, 23, 42)); // Deep sleek slate-900

                if (IsPlaying)
                {
                    // Pause icon: two vertical bars
                    int barW = 4;
                    int barH = 14;
                    int gap = 4;
                    g.FillRectangle(iconBrush, cx - barW - gap / 2, cy - barH / 2, barW, barH);
                    g.FillRectangle(iconBrush, cx + gap / 2, cy - barH / 2, barW, barH);
                }
                else
                {
                    // Play icon: equilateral triangle
                    int triH = 14;
                    int triW = 13;
                    Point[] pts = {
                        new Point(cx - triW / 2 + 2, cy - triH / 2),
                        new Point(cx + triW / 2 + 2, cy),
                        new Point(cx - triW / 2 + 2, cy + triH / 2)
                    };
                    g.FillPolygon(iconBrush, pts);
                }
            }
            else
            {
                // Circular glass button
                if (_isHovered)
                {
                    Color hoverBg = IconType == "close"
                        ? Color.FromArgb(200, 239, 68, 68) // subtle red on close hover
                        : Color.FromArgb(40, 255, 255, 255);
                    using var hBrush = new SolidBrush(hoverBg);
                    g.FillEllipse(hBrush, rect);
                }

                int cx = Width / 2;
                int cy = Height / 2;
                Color iconColor = _isHovered ? Color.White : Color.FromArgb(148, 163, 184);

                using var pen = new Pen(iconColor, 1.8f);
                pen.StartCap = LineCap.Round;
                pen.EndCap = LineCap.Round;
                using var brush = new SolidBrush(iconColor);

                if (IconType == "prev")
                {
                    g.DrawLine(pen, cx - 6, cy - 6, cx - 6, cy + 6);
                    Point[] pts = {
                        new Point(cx + 6, cy - 6),
                        new Point(cx - 3, cy),
                        new Point(cx + 6, cy + 6)
                    };
                    g.FillPolygon(brush, pts);
                }
                else if (IconType == "next")
                {
                    Point[] pts = {
                        new Point(cx - 6, cy - 6),
                        new Point(cx + 3, cy),
                        new Point(cx - 6, cy + 6)
                    };
                    g.FillPolygon(brush, pts);
                    g.DrawLine(pen, cx + 6, cy - 6, cx + 6, cy + 6);
                }
                else if (IconType == "vol")
                {
                    Color volColor = IsMuted ? Color.FromArgb(244, 63, 94) : (_isHovered ? Color.White : Color.FromArgb(148, 163, 184));
                    using var vPen = new Pen(volColor, 1.6f);
                    using var vBrush = new SolidBrush(volColor);
                    Point[] cone = {
                        new Point(cx - 6, cy - 3),
                        new Point(cx - 3, cy - 3),
                        new Point(cx + 1, cy - 6),
                        new Point(cx + 1, cy + 6),
                        new Point(cx - 3, cy + 3),
                        new Point(cx - 6, cy + 3)
                    };
                    g.FillPolygon(vBrush, cone);
                    if (IsMuted)
                    {
                        g.DrawLine(vPen, cx + 4, cy - 4, cx + 9, cy + 4);
                        g.DrawLine(vPen, cx + 9, cy - 4, cx + 4, cy + 4);
                    }
                    else
                    {
                        g.DrawArc(vPen, cx - 1, cy - 4, 6, 8, -50, 100);
                        g.DrawArc(vPen, cx + 1, cy - 7, 10, 14, -55, 110);
                    }
                }
                else if (IconType == "pin")
                {
                    Color pinColor = IsPinned ? AccentColor : (_isHovered ? Color.White : Color.FromArgb(100, 116, 139));
                    using var pPen = new Pen(pinColor, 1.6f);
                    using var pBrush = new SolidBrush(pinColor);
                    g.DrawLine(pPen, cx - 2, cy + 2, cx - 5, cy + 5);
                    Point[] pinHead = {
                        new Point(cx - 3, cy - 3),
                        new Point(cx + 3, cy - 3),
                        new Point(cx + 1, cy + 2),
                        new Point(cx - 1, cy + 2)
                    };
                    g.FillPolygon(pBrush, pinHead);
                    g.DrawLine(pPen, cx - 4, cy - 4, cx + 4, cy - 4);
                }
                else if (IconType == "restore")
                {
                    g.DrawRectangle(pen, cx - 4, cy - 4, 9, 9);
                }
                else if (IconType == "close")
                {
                    g.DrawLine(pen, cx - 4, cy - 4, cx + 4, cy + 4);
                    g.DrawLine(pen, cx + 4, cy - 4, cx - 4, cy + 4);
                }
            }
        }
    }

    public class RoundedProgressBar : Panel
    {
        [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public Color ProgressColor { get; set; } = Color.FromArgb(16, 185, 129);

        [DesignerSerializationVisibility(DesignerSerializationVisibility.Hidden)]
        public double ProgressPercentage { get; set; } = 0.0;

        public RoundedProgressBar()
        {
            DoubleBuffered = true;
            Height = 5;
            BackColor = Color.Transparent;
            Cursor = Cursors.Hand;
        }

        protected override void OnPaint(PaintEventArgs pe)
        {
            var g = pe.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            // Translucent glass track
            var trackRect = new Rectangle(0, 0, Width, Height);
            using var trackBrush = new SolidBrush(Color.FromArgb(40, 255, 255, 255));
            using var trackPath = MiniPlayerForm.CreateRoundRectPath(trackRect, 2);
            g.FillPath(trackBrush, trackPath);

            // Active fill
            int barWidth = (int)(Math.Max(0.0, Math.Min(1.0, ProgressPercentage)) * Width);
            if (barWidth > 2)
            {
                var fillRect = new Rectangle(0, 0, barWidth, Height);
                using var fillBrush = new SolidBrush(ProgressColor);
                using var fillPath = MiniPlayerForm.CreateRoundRectPath(fillRect, 2);
                g.FillPath(fillBrush, fillPath);
            }
        }
    }
    #endregion

    public class MiniPlayerForm : Form
    {
        #region Win32 Native APIs & DWM Rounding
        [DllImport("user32.dll")]
        private static extern bool ReleaseCapture();
        [DllImport("user32.dll")]
        private static extern int SendMessage(IntPtr hWnd, int Msg, int wParam, int lParam);

        [DllImport("dwmapi.dll")]
        private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int attrValue, int attrSize);

        [DllImport("gdi32.dll")]
        private static extern IntPtr CreateRoundRectRgn(int nLeftRect, int nTopRect, int nRightRect, int nBottomRect, int nWidthEllipse, int nHeightEllipse);
        [DllImport("gdi32.dll")]
        private static extern bool DeleteObject(IntPtr hObject);

        private const int DWMWA_WINDOW_CORNER_PREFERENCE = 33;
        private const int DWMWCP_ROUND = 2;

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

        private readonly RoundedPictureBox _coverPic;
        private readonly Label _titleLabel;
        private readonly Label _artistLabel;
        private readonly Label _timeLabel;
        private readonly RoundedProgressBar _progressBar;
        private readonly CircularIconButton _prevBtn;
        private readonly CircularIconButton _playBtn;
        private readonly CircularIconButton _nextBtn;
        private readonly CircularIconButton _volBtn;
        private readonly CircularIconButton _pinBtn;
        private readonly CircularIconButton _restoreBtn;
        private readonly CircularIconButton _closeBtn;

        private bool _isPlaying = false;
        private bool _isPinned = true;
        private bool _isMuted = false;
        private double _currentSeconds = 0;
        private double _totalSeconds = 0;

        // Dynamic mood color palette
        private Color _accentColor = Color.FromArgb(16, 185, 129); // emerald-500
        private Color _ambientDark = Color.FromArgb(15, 23, 42);   // slate-900
        private readonly Color _ambientEnd = Color.FromArgb(10, 15, 26);  // slate-950

        private static readonly HttpClient _httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };

        public MiniPlayerForm()
        {
            FormBorderStyle = FormBorderStyle.None;
            TopMost = true;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            Size = new Size(450, 140);
            MinimumSize = new Size(360, 120);
            MaximumSize = new Size(820, 320);
            DoubleBuffered = true;

            // Position at bottom-right corner of primary screen
            var workArea = Screen.PrimaryScreen?.WorkingArea ?? new Rectangle(0, 0, 1920, 1080);
            Location = new Point(workArea.Right - 470, workArea.Bottom - 165);

            // Rounded Cover Art
            _coverPic = new RoundedPictureBox
            {
                Size = new Size(86, 86),
                Location = new Point(16, 16),
                Cursor = Cursors.Hand
            };
            _coverPic.Click += (s, e) => OnRestoreMainWindow?.Invoke();
            Controls.Add(_coverPic);

            // Title Label
            _titleLabel = new Label
            {
                Location = new Point(116, 14),
                Size = new Size(220, 22),
                ForeColor = Color.White,
                BackColor = Color.Transparent,
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
                Location = new Point(116, 38),
                Size = new Size(220, 18),
                ForeColor = Color.FromArgb(148, 163, 184), // slate-400
                BackColor = Color.Transparent,
                Font = new Font("Segoe UI", 8.5f, FontStyle.Regular),
                AutoEllipsis = true,
                Text = "YouTube & Spotify Downloader"
            };
            Controls.Add(_artistLabel);

            // Seekable Rounded Progress Bar
            _progressBar = new RoundedProgressBar
            {
                Location = new Point(116, 64),
                Size = new Size(220, 5),
                ProgressColor = _accentColor
            };
            _progressBar.MouseClick += OnProgressTrackClick;
            Controls.Add(_progressBar);

            // Time Label
            _timeLabel = new Label
            {
                Location = new Point(116, 73),
                Size = new Size(110, 16),
                ForeColor = Color.FromArgb(100, 116, 139), // slate-500
                BackColor = Color.Transparent,
                Font = new Font("Segoe UI", 7.5f, FontStyle.Bold),
                Text = "00:00 / 00:00"
            };
            Controls.Add(_timeLabel);

            // Top action buttons (Pin, Restore, Close)
            _pinBtn = new CircularIconButton
            {
                Size = new Size(26, 26),
                Location = new Point(345, 10),
                IconType = "pin",
                IsPinned = _isPinned,
                AccentColor = _accentColor
            };
            _pinBtn.Click += (s, e) => TogglePin();
            Controls.Add(_pinBtn);

            _restoreBtn = new CircularIconButton
            {
                Size = new Size(26, 26),
                Location = new Point(375, 10),
                IconType = "restore"
            };
            _restoreBtn.Click += (s, e) => OnRestoreMainWindow?.Invoke();
            Controls.Add(_restoreBtn);

            _closeBtn = new CircularIconButton
            {
                Size = new Size(26, 26),
                Location = new Point(405, 10),
                IconType = "close"
            };
            _closeBtn.Click += (s, e) => Hide();
            Controls.Add(_closeBtn);

            // Sleek circular playback controls
            _prevBtn = new CircularIconButton
            {
                Size = new Size(32, 32),
                Location = new Point(116, 95),
                IconType = "prev"
            };
            _prevBtn.Click += (s, e) => OnCommand?.Invoke("prev");
            Controls.Add(_prevBtn);

            _playBtn = new CircularIconButton
            {
                Size = new Size(40, 40),
                Location = new Point(154, 91),
                IsPlayButton = true,
                IsPlaying = false,
                AccentColor = _accentColor
            };
            _playBtn.Click += (s, e) =>
            {
                if (_isPlaying) OnCommand?.Invoke("pause");
                else OnCommand?.Invoke("play");
            };
            Controls.Add(_playBtn);

            _nextBtn = new CircularIconButton
            {
                Size = new Size(32, 32),
                Location = new Point(200, 95),
                IconType = "next"
            };
            _nextBtn.Click += (s, e) => OnCommand?.Invoke("next");
            Controls.Add(_nextBtn);

            _volBtn = new CircularIconButton
            {
                Size = new Size(30, 30),
                Location = new Point(242, 96),
                IconType = "vol",
                IsMuted = _isMuted
            };
            _volBtn.Click += (s, e) => OnCommand?.Invoke("mute");
            Controls.Add(_volBtn);

            // Enable dragging form on background and labels
            MouseDown += OnFormMouseDown;
            _titleLabel.MouseDown += OnFormMouseDown;
            _artistLabel.MouseDown += OnFormMouseDown;
            _timeLabel.MouseDown += OnFormMouseDown;

            UpdateWindowRegion();
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            try
            {
                // Native Windows 11 rounded corners attribute
                int preference = DWMWCP_ROUND;
                DwmSetWindowAttribute(Handle, DWMWA_WINDOW_CORNER_PREFERENCE, ref preference, sizeof(int));
            }
            catch { }
            UpdateWindowRegion();
        }

        public static GraphicsPath CreateRoundRectPath(Rectangle bounds, int radius)
        {
            var path = new GraphicsPath();
            int d = radius * 2;
            if (d > bounds.Width) d = bounds.Width;
            if (d > bounds.Height) d = bounds.Height;
            if (d <= 0)
            {
                path.AddRectangle(bounds);
                return path;
            }
            path.AddArc(bounds.X, bounds.Y, d, d, 180, 90);
            path.AddArc(bounds.Right - d, bounds.Y, d, d, 270, 90);
            path.AddArc(bounds.Right - d, bounds.Bottom - d, d, d, 0, 90);
            path.AddArc(bounds.X, bounds.Bottom - d, d, d, 90, 90);
            path.CloseFigure();
            return path;
        }

        private void UpdateWindowRegion()
        {
            try
            {
                IntPtr hRgn = CreateRoundRectRgn(0, 0, Width + 1, Height + 1, 22, 22);
                if (hRgn != IntPtr.Zero)
                {
                    Region = Region.FromHrgn(hRgn);
                    DeleteObject(hRgn);
                }
            }
            catch { }
        }

        private void TogglePin()
        {
            _isPinned = !_isPinned;
            TopMost = _isPinned;
            _pinBtn.IsPinned = _isPinned;
            _pinBtn.Invalidate();
        }

        private void OnProgressTrackClick(object? sender, MouseEventArgs e)
        {
            if (_totalSeconds <= 0) return;
            Point p = _progressBar.PointToClient(Cursor.Position);
            double ratio = Math.Max(0.0, Math.Min(1.0, (double)p.X / _progressBar.Width));
            double seekTime = ratio * _totalSeconds;
            OnCommand?.Invoke($"seek:{seekTime:F2}");
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            UpdateWindowRegion();

            int rightEdge = ClientSize.Width;
            int bottomEdge = ClientSize.Height;

            if (_closeBtn != null) _closeBtn.Location = new Point(rightEdge - 34, 10);
            if (_restoreBtn != null) _restoreBtn.Location = new Point(rightEdge - 64, 10);
            if (_pinBtn != null) _pinBtn.Location = new Point(rightEdge - 94, 10);

            int textWidth = Math.Max(120, rightEdge - 120 - 95);
            if (_titleLabel != null) _titleLabel.Width = textWidth;
            if (_artistLabel != null) _artistLabel.Width = textWidth;

            int progressWidth = Math.Max(120, rightEdge - 120 - 20);
            if (_progressBar != null)
            {
                _progressBar.Width = progressWidth;
                _progressBar.Invalidate();
            }

            int controlsY = Math.Max(70, bottomEdge - 45);
            if (_prevBtn != null) _prevBtn.Location = new Point(116, controlsY + 4);
            if (_playBtn != null) _playBtn.Location = new Point(154, controlsY);
            if (_nextBtn != null) _nextBtn.Location = new Point(200, controlsY + 4);
            if (_volBtn != null) _volBtn.Location = new Point(242, controlsY + 5);

            if (_timeLabel != null)
            {
                _timeLabel.Location = new Point(Math.Max(280, rightEdge - 120), controlsY + 12);
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

        protected override void OnPaintBackground(PaintEventArgs pe)
        {
            var g = pe.Graphics;
            using var brush = new LinearGradientBrush(
                ClientRectangle,
                _ambientDark,
                _ambientEnd,
                50f
            );
            g.FillRectangle(brush, ClientRectangle);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            using var path = CreateRoundRectPath(new Rectangle(0, 0, Width - 1, Height - 1), 12);
            using var pen = new Pen(Color.FromArgb(80, _accentColor.R, _accentColor.G, _accentColor.B), 1.2f);
            e.Graphics.DrawPath(pen, path);
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
                Task.Run(async () =>
                {
                    try
                    {
                        byte[] data = await _httpClient.GetByteArrayAsync(coverUrl);
                        using var ms = new MemoryStream(data);
                        var loadedImg = Image.FromStream(ms);
                        Invoke(() =>
                        {
                            _coverPic.Image?.Dispose();
                            _coverPic.Image = loadedImg;
                            ExtractDominantColor(loadedImg);
                            _coverPic.Invalidate();
                        });
                    }
                    catch
                    {
                        Invoke(() =>
                        {
                            _coverPic.Image = null;
                            ResetDefaultTheme();
                        });
                    }
                });
            }
            else
            {
                _coverPic.Image = null;
                ResetDefaultTheme();
            }
        }

        private void ExtractDominantColor(Image img)
        {
            try
            {
                using var thumb = new Bitmap(img, new Size(24, 24));
                Color bestColor = Color.FromArgb(16, 185, 129);
                float maxScore = 0;

                for (int x = 0; x < thumb.Width; x++)
                {
                    for (int y = 0; y < thumb.Height; y++)
                    {
                        Color c = thumb.GetPixel(x, y);
                        float sat = c.GetSaturation();
                        float bri = c.GetBrightness();

                        if (sat > 0.25f && bri > 0.25f && bri < 0.85f)
                        {
                            float score = sat * (1.0f - Math.Abs(bri - 0.55f));
                            if (score > maxScore)
                            {
                                maxScore = score;
                                bestColor = c;
                            }
                        }
                    }
                }

                _accentColor = bestColor;

                // Subtle dark ambient glass tint
                int darkR = (int)(_accentColor.R * 0.16 + 11 * 0.84);
                int darkG = (int)(_accentColor.G * 0.16 + 15 * 0.84);
                int darkB = (int)(_accentColor.B * 0.16 + 25 * 0.84);
                _ambientDark = Color.FromArgb(darkR, darkG, darkB);

                ApplyTheme();
            }
            catch
            {
                ResetDefaultTheme();
            }
        }

        private void ResetDefaultTheme()
        {
            _accentColor = Color.FromArgb(16, 185, 129);
            _ambientDark = Color.FromArgb(15, 23, 42);
            ApplyTheme();
        }

        private void ApplyTheme()
        {
            if (InvokeRequired)
            {
                Invoke(ApplyTheme);
                return;
            }

            _playBtn.AccentColor = _accentColor;
            _pinBtn.AccentColor = _accentColor;
            _progressBar.ProgressColor = _accentColor;
            _playBtn.Invalidate();
            _pinBtn.Invalidate();
            _progressBar.Invalidate();
            Invalidate();
        }

        public void UpdatePlayState(bool isPlaying)
        {
            if (InvokeRequired)
            {
                Invoke(() => UpdatePlayState(isPlaying));
                return;
            }

            _isPlaying = isPlaying;
            _playBtn.IsPlaying = isPlaying;
            _playBtn.Invalidate();
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

            if (_totalSeconds > 0)
            {
                _progressBar.ProgressPercentage = _currentSeconds / _totalSeconds;
            }
            else
            {
                _progressBar.ProgressPercentage = 0.0;
            }
            _progressBar.Invalidate();
        }

        public void UpdateVolumeState(bool isMuted)
        {
            if (InvokeRequired)
            {
                Invoke(() => UpdateVolumeState(isMuted));
                return;
            }

            _isMuted = isMuted;
            _volBtn.IsMuted = isMuted;
            _volBtn.Invalidate();
        }

        private static string FormatTime(int seconds)
        {
            int m = seconds / 60;
            int s = seconds % 60;
            return $"{m:D2}:{s:D2}";
        }
    }
}
