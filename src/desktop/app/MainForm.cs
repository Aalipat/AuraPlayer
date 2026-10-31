using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace YouTubeDownloaderApp
{
    #region Windows Job Object Helper
    public static class ChildProcessTracker
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
        private static extern IntPtr CreateJobObject(IntPtr a, string? lpName);

        [DllImport("kernel32.dll")]
        private static extern bool SetInformationJobObject(IntPtr hJob, int JobObjectInfoClass, IntPtr lpJobObjectInfo, uint cbJobObjectInfoLength);

        [DllImport("kernel32.dll")]
        private static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

        [StructLayout(LayoutKind.Sequential)]
        private struct IO_COUNTERS
        {
            public ulong ReadOperationCount;
            public ulong WriteOperationCount;
            public ulong OtherOperationCount;
            public ulong ReadTransferCount;
            public ulong WriteTransferCount;
            public ulong OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit;
            public long PerJobUserTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryLimit;
            public UIntPtr PeakJobMemoryLimit;
        }

        private static readonly IntPtr s_jobHandle;

        static ChildProcessTracker()
        {
            try
            {
                s_jobHandle = CreateJobObject(IntPtr.Zero, null);
                var extendedInfo = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION
                {
                    BasicLimitInformation = new JOBOBJECT_BASIC_LIMIT_INFORMATION
                    {
                        LimitFlags = 0x2000 // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    }
                };

                int length = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
                IntPtr extendedInfoPtr = Marshal.AllocHGlobal(length);
                try
                {
                    Marshal.StructureToPtr(extendedInfo, extendedInfoPtr, false);
                    SetInformationJobObject(s_jobHandle, 9, extendedInfoPtr, (uint)length);
                }
                finally
                {
                    Marshal.FreeHGlobal(extendedInfoPtr);
                }
            }
            catch { }
        }

        public static void AddProcess(Process? process)
        {
            if (s_jobHandle != IntPtr.Zero && process != null && !process.HasExited)
            {
                try
                {
                    AssignProcessToJobObject(s_jobHandle, process.Handle);
                }
                catch { }
            }
        }
    }
    #endregion

    public class MainForm : Form
    {
        private const int WM_CLIPBOARDUPDATE = 0x031D;

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool AddClipboardFormatListener(IntPtr hwnd);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool RemoveClipboardFormatListener(IntPtr hwnd);

        [DllImport("psapi.dll")]
        private static extern int EmptyWorkingSet(IntPtr hwProc);

        public static void TrimMemory()
        {
            try
            {
                GC.Collect(2, GCCollectionMode.Forced, true);
                GC.WaitForPendingFinalizers();
                EmptyWorkingSet(Process.GetCurrentProcess().Handle);
            }
            catch { }
        }

        private WebView2? _webView;
        private Panel? _splashPanel;
        public const string WakeupEventName = "Global\\YouTube_Spotify_Downloader_WakeupEvent";
        private static readonly HttpClient s_httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(1) };

        private Label? _splashStatusLabel;
        private ProgressBar? _splashProgressBar;
        private NotifyIcon? _trayIcon;
        private ContextMenuStrip? _trayMenu;
        private MiniPlayerForm? _miniPlayer;
        private Process? _pythonProcess;
        private EventWaitHandle? _wakeupEvent;
        private Thread? _wakeupThread;
        private bool _isExiting = false;
        private bool _isClipboardListening = false;
        private string _lastDetectedUrl = string.Empty;
        private readonly string _baseDir;
        private readonly string _serverUrl = "http://127.0.0.1:8000";

        private static readonly Regex MediaUrlRegex = new Regex(
            @"(https?://)?(www\.|m\.|music\.)?(youtube\.com/(watch\?.*v=|shorts/|embed/|live/)|youtu\.be/|open\.spotify\.com/(track|album|playlist)/)[a-zA-Z0-9_\-\?=&]+",
            RegexOptions.IgnoreCase | RegexOptions.Compiled
        );

        public MainForm()
        {
            // Determine base directory dynamically by scanning up for app_icon.ico
            string current = AppDomain.CurrentDomain.BaseDirectory;
            string found = current;
            var dir = new DirectoryInfo(current);
            while (dir != null)
            {
                if (File.Exists(Path.Combine(dir.FullName, "app_icon.ico")))
                {
                    found = dir.FullName;
                    break;
                }
                dir = dir.Parent;
            }
            _baseDir = found;

            InitializeComponents();
        }

        private void InitializeComponents()
        {
            Text = "YouTube & Spotify Downloader | دانلودر یوتیوب و اسپاتیفای";
            Size = new Size(1220, 840);
            MinimumSize = new Size(920, 640);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(15, 23, 42); // Slate 900 #0F172A

            // Set Icon
            string iconPath = Path.Combine(_baseDir, "app_icon.ico");
            if (File.Exists(iconPath))
            {
                try
                {
                    Icon = new Icon(iconPath);
                }
                catch { }
            }

            // Setup System Tray
            _trayMenu = new ContextMenuStrip();
            _trayMenu.Items.Add("📌 نمایش پنجره اصلی", null, (s, e) => RestoreWindow());
            _trayMenu.Items.Add("📁 باز کردن پوشه دانلودها", null, (s, e) => OpenDownloadsFolder());
            _trayMenu.Items.Add("🔄 بارگذاری مجدد پنل", null, (s, e) => _webView?.Reload());
            _trayMenu.Items.Add(new ToolStripSeparator());
            _trayMenu.Items.Add("❌ خروج کامل (Exit)", null, (s, e) => ExitApplication());

            _trayIcon = new NotifyIcon
            {
                Text = "دانلودر یوتیوب و اسپاتیفای",
                Icon = Icon ?? SystemIcons.Application,
                ContextMenuStrip = _trayMenu,
                Visible = true
            };
            _trayIcon.DoubleClick += (s, e) => RestoreWindow();

            // Setup Sleek Splash / Loading Screen
            _splashPanel = new Panel
            {
                Dock = DockStyle.Fill,
                BackColor = Color.FromArgb(15, 23, 42),
            };

            var splashTitle = new Label
            {
                Text = "YouTube & Spotify Downloader",
                Font = new Font("Segoe UI", 18, FontStyle.Bold),
                ForeColor = Color.White,
                AutoSize = false,
                TextAlign = ContentAlignment.MiddleCenter,
                Dock = DockStyle.Top,
                Height = 60,
                Padding = new Padding(0, 20, 0, 0)
            };

            _splashStatusLabel = new Label
            {
                Text = "در حال راه‌اندازی هسته نرم‌افزار و اتصال به سرور محلی...",
                Font = new Font("Segoe UI", 11, FontStyle.Regular),
                ForeColor = Color.FromArgb(148, 163, 184), // Slate 400
                AutoSize = false,
                TextAlign = ContentAlignment.MiddleCenter,
                Dock = DockStyle.Top,
                Height = 40,
                RightToLeft = RightToLeft.Yes
            };

            _splashProgressBar = new ProgressBar
            {
                Style = ProgressBarStyle.Marquee,
                MarqueeAnimationSpeed = 30,
                Height = 6,
                Dock = DockStyle.Bottom
            };

            var centerContainer = new Panel
            {
                Size = new Size(500, 150),
                BackColor = Color.Transparent
            };
            centerContainer.Controls.Add(_splashStatusLabel);
            centerContainer.Controls.Add(splashTitle);
            centerContainer.Controls.Add(_splashProgressBar);

            _splashPanel.Resize += (s, e) =>
            {
                centerContainer.Location = new Point(
                    (_splashPanel.Width - centerContainer.Width) / 2,
                    (_splashPanel.Height - centerContainer.Height) / 2
                );
            };
            _splashPanel.Controls.Add(centerContainer);
            Controls.Add(_splashPanel);

            // Setup WebView2
            _webView = new WebView2
            {
                Dock = DockStyle.Fill,
                DefaultBackgroundColor = Color.FromArgb(15, 23, 42),
                Visible = false
            };
            Controls.Add(_webView);

            Load += async (s, e) => await OnFormLoaded();
        }

        private async Task OnFormLoaded()
        {
            // Start Single-Instance Wakeup Listener
            try
            {
                _wakeupEvent = new EventWaitHandle(false, EventResetMode.AutoReset, WakeupEventName);
                _wakeupThread = new Thread(() =>
                {
                    while (!_isExiting && _wakeupEvent != null)
                    {
                        try
                        {
                            if (_wakeupEvent.WaitOne())
                            {
                                if (_isExiting) break;
                                BeginInvoke(new Action(RestoreWindow));
                            }
                        }
                        catch { break; }
                    }
                })
                {
                    IsBackground = true,
                    Name = "SingleInstanceWakeupListener"
                };
                _wakeupThread.Start();
            }
            catch { }

            // Register Windows Clipboard Format Listener
            try
            {
                if (AddClipboardFormatListener(Handle))
                {
                    _isClipboardListening = true;
                }
            }
            catch { }

            // 1. Ensure Python Backend is Running
            UpdateSplashStatus("در حال اجرای سرویس دانلود و راه‌اندازی پایتون...");
            bool backendReady = await EnsurePythonBackendAsync();

            if (!backendReady)
            {
                string logFile = Path.Combine(_baseDir, "data", "server.log");
                string errorDetails = "";
                if (File.Exists(logFile))
                {
                    try
                    {
                        string[] lines = File.ReadAllLines(logFile);
                        int start = Math.Max(0, lines.Length - 10);
                        errorDetails = "\n\nجزئیات خطا:\n" + string.Join("\n", lines[start..]);
                    }
                    catch { }
                }

                MessageBox.Show(
                    "امکان اتصال به سرور محلی پایتون وجود ندارد." + errorDetails,
                    "خطا در راه‌اندازی",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                ExitApplication();
                return;
            }

            // 2. Initialize WebView2 with dedicated UserDataFolder
            UpdateSplashStatus("در حال آماده‌سازی رابط کاربری مدرن...");
            await InitializeWebViewAsync();
        }

        private void UpdateSplashStatus(string text)
        {
            if (_splashStatusLabel != null && !IsDisposed)
            {
                Invoke(() => _splashStatusLabel.Text = text);
            }
        }

        private async Task<bool> EnsurePythonBackendAsync()
        {
            if (await IsServerRespondingAsync())
            {
                return true; // Server already running
            }

            string serverPy = Path.Combine(_baseDir, "src", "panel", "server.py");
            if (!File.Exists(serverPy))
            {
                MessageBox.Show(
                    "فایل server.py در مسیر src/panel پیدا نشد!\n" + serverPy,
                    "خطا در ساختار برنامه",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return false;
            }

            string pythonExe = Path.Combine(_baseDir, "runtime", "pythonw.exe");
            if (!File.Exists(pythonExe))
            {
                pythonExe = Path.Combine(_baseDir, "runtime", "python.exe");
            }

            if (!File.Exists(pythonExe))
            {
                MessageBox.Show(
                    "فایل مفسر پایتون در پوشه runtime پیدا نشد!\n" + _baseDir,
                    "خطا در اجرای هسته",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return false;
            }

            ProcessStartInfo psi = new ProcessStartInfo
            {
                FileName = pythonExe,
                Arguments = $"\"{serverPy}\"",
                WorkingDirectory = _baseDir,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };

            // Set PATH to prioritize bin/ folder for local ffmpeg/ffprobe
            string binDir = Path.Combine(_baseDir, "bin");
            string currentPath = Environment.GetEnvironmentVariable("PATH") ?? "";
            psi.EnvironmentVariables["PATH"] = binDir + ";" + currentPath;
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            psi.EnvironmentVariables["PORT"] = "8000";

            try
            {
                _pythonProcess = Process.Start(psi);
                ChildProcessTracker.AddProcess(_pythonProcess);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "خطا در اجرای سرور پایتون:\n" + ex.Message,
                    "خطای اجرا",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return false;
            }

            // Wait for server to respond (up to 20 seconds)
            for (int i = 0; i < 40; i++)
            {
                await Task.Delay(500);
                if (await IsServerRespondingAsync())
                {
                    return true;
                }

                if (_pythonProcess != null && _pythonProcess.HasExited)
                {
                    return false;
                }
            }

            return false;
        }

        private async Task<bool> IsServerRespondingAsync()
        {
            try
            {
                var res = await s_httpClient.GetAsync(_serverUrl + "/api/system/info");
                return res.IsSuccessStatusCode;
            }
            catch
            {
                return false;
            }
        }

        private async Task InitializeWebViewAsync()
        {
            if (_webView == null) return;

            string userDataDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "YouTubeSpotifyDownloader",
                "WebViewData"
            );
            Directory.CreateDirectory(userDataDir);

            var options = new CoreWebView2EnvironmentOptions(
                "--renderer-process-limit=1 " +
                "--disable-background-networking " +
                "--disable-features=Translate,OptimizationGuideModelDownloading,MediaRouter " +
                "--enable-features=HardwareMediaKeyHandling"
            );
            var env = await CoreWebView2Environment.CreateAsync(null, userDataDir, options);
            await _webView.EnsureCoreWebView2Async(env);

            // Clean Native App Experience
            _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = false;
            _webView.CoreWebView2.Settings.AreDevToolsEnabled = false;
            _webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
            _webView.CoreWebView2.Settings.IsZoomControlEnabled = false;

            // Handle Web Messages sent from JavaScript
            _webView.CoreWebView2.WebMessageReceived += OnWebMessageReceived;

            // Hide Splash and Show WebView on completion
            _webView.NavigationCompleted += (s, e) =>
            {
                if (e.IsSuccess)
                {
                    if (_splashPanel != null)
                    {
                        _splashPanel.Visible = false;
                    }
                    _webView.Visible = true;
                    _webView.BringToFront();
                }
                else
                {
                    UpdateSplashStatus("خطا در بارگذاری پنل وب‌ویو. در حال تلاش...");
                }
            };

            // Navigate to panel
            _webView.CoreWebView2.Navigate(_serverUrl);
        }

        private void OnWebMessageReceived(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
        {
            try
            {
                string msg = e.TryGetWebMessageAsString();
                if (msg.StartsWith("toast:"))
                {
                    string text = msg.Substring("toast:".Length);
                    _trayIcon?.ShowBalloonTip(3000, "دانلودر یوتیوب و اسپاتیفای", text, ToolTipIcon.Info);
                }
                else if (msg == "open_downloads")
                {
                    OpenDownloadsFolder();
                }
                else if (msg == "pick_folder")
                {
                    Invoke(() =>
                    {
                        using var fbd = new FolderBrowserDialog();
                        fbd.Description = "پوشه دلخواه جهت ذخیره دانلودها را انتخاب کنید";
                        fbd.UseDescriptionForTitle = true;
                        fbd.ShowNewFolderButton = true;
                        if (fbd.ShowDialog(this) == DialogResult.OK)
                        {
                            string selected = fbd.SelectedPath;
                            string escaped = HttpUtility.JavaScriptStringEncode(selected);
                            _webView?.ExecuteScriptAsync($"onNativeFolderChosen('{escaped}')");
                        }
                    });
                }
                else if (msg.StartsWith("mini_player:"))
                {
                    HandleMiniPlayerMessage(msg.Substring("mini_player:".Length));
                }
            }
            catch { }
        }

        private void HandleMiniPlayerMessage(string data)
        {
            Invoke(() =>
            {
                try
                {
                    if (data.StartsWith("open|"))
                    {
                        var parts = data.Substring("open|".Length).Split('|');
                        string title = "نام قطعه";
                        string artist = "هنرمند";
                        bool playing = false;
                        string cover = "";

                        foreach (var part in parts)
                        {
                            var kv = part.Split(new[] { '=' }, 2);
                            if (kv.Length == 2)
                            {
                                string key = kv[0];
                                string val = Uri.UnescapeDataString(kv[1]);
                                if (key == "title") title = val;
                                else if (key == "artist") artist = val;
                                else if (key == "playing") playing = val.Equals("true", StringComparison.OrdinalIgnoreCase);
                                else if (key == "cover") cover = val;
                            }
                        }

                        if (!string.IsNullOrEmpty(cover) && cover.StartsWith("/"))
                        {
                            cover = _serverUrl + cover;
                        }

                        if (_miniPlayer == null || _miniPlayer.IsDisposed)
                        {
                            _miniPlayer = new MiniPlayerForm();
                            _miniPlayer.OnCommand += cmd =>
                            {
                                Invoke(() =>
                                {
                                    _webView?.ExecuteScriptAsync($"window.miniPlayerCommand('{cmd}')");
                                });
                            };
                            _miniPlayer.OnRestoreMainWindow += () =>
                            {
                                Invoke(() =>
                                {
                                    RestoreWindow();
                                });
                            };
                        }

                        _miniPlayer.UpdateInfo(title, artist, playing, cover);
                        if (!_miniPlayer.Visible)
                        {
                            _miniPlayer.Show();
                        }
                        _miniPlayer.BringToFront();
                    }
                    else if (data.StartsWith("state|"))
                    {
                        if (_miniPlayer != null && !_miniPlayer.IsDisposed && _miniPlayer.Visible)
                        {
                            bool playing = data.Contains("playing=true");
                            _miniPlayer.UpdatePlayState(playing);
                        }
                    }
                    else if (data == "close")
                    {
                        if (_miniPlayer != null && !_miniPlayer.IsDisposed)
                        {
                            _miniPlayer.Hide();
                        }
                    }
                }
                catch { }
            });
        }

        protected override void WndProc(ref Message m)
        {
            base.WndProc(ref m);

            if (m.Msg == WM_CLIPBOARDUPDATE)
            {
                OnClipboardChanged();
            }
        }

        private void OnClipboardChanged()
        {
            try
            {
                if (Clipboard.ContainsText())
                {
                    string text = Clipboard.GetText().Trim();
                    if (!string.IsNullOrEmpty(text))
                    {
                        var match = MediaUrlRegex.Match(text);
                        if (match.Success)
                        {
                            string url = match.Value;
                            if (url != _lastDetectedUrl)
                            {
                                _lastDetectedUrl = url;
                                // Inject into web page input box automatically
                                if (_webView?.CoreWebView2 != null)
                                {
                                    string escaped = HttpUtility.JavaScriptStringEncode(url);
                                    string js = $@"
                                        (function() {{
                                            const input = document.getElementById('urlInput');
                                            if (input) {{
                                                input.value = '{escaped}';
                                                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                            }}
                                        }})();
                                    ";
                                    _webView.ExecuteScriptAsync(js);

                                    _trayIcon?.ShowBalloonTip(
                                        4000,
                                        "لینک شناسایی شد",
                                        "لینک کپی‌شده از یوتیوب/اسپاتیفای در برنامه جای‌گذاری شد.",
                                        ToolTipIcon.Info
                                    );
                                }
                            }
                        }
                    }
                }
            }
            catch { }
        }

        protected override void OnResize(EventArgs e)
        {
            base.OnResize(e);
            if (WindowState == FormWindowState.Minimized)
            {
                Hide();
                TrimMemory();
            }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (!_isExiting && e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Hide();
                TrimMemory();
                _trayIcon?.ShowBalloonTip(
                    4000,
                    "دانلودر یوتیوب و اسپاتیفای",
                    "برنامه به پس‌زمینه منتقل شد و دانلودها در حال اجرا هستند.\nبرای باز کردن روی آیکون کنار ساعت کلیک کنید.",
                    ToolTipIcon.Info
                );
                return;
            }

            base.OnFormClosing(e);
            Cleanup();
        }

        public void RestoreWindow()
        {
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
            BringToFront();
        }

        private void OpenDownloadsFolder()
        {
            string downloadDir = Path.Combine(_baseDir, "data", "downloads");
            Directory.CreateDirectory(downloadDir);
            Process.Start(new ProcessStartInfo
            {
                FileName = "explorer.exe",
                Arguments = downloadDir,
                UseShellExecute = true
            });
        }

        private void ExitApplication()
        {
            _isExiting = true;
            Cleanup();
            Application.Exit();
        }

        private void Cleanup()
        {
            try
            {
                _wakeupEvent?.Set();
                _wakeupEvent?.Dispose();
                _wakeupEvent = null;
            }
            catch { }

            if (_isClipboardListening)
            {
                try { RemoveClipboardFormatListener(Handle); } catch { }
                _isClipboardListening = false;
            }

            if (_trayIcon != null)
            {
                _trayIcon.Visible = false;
                _trayIcon.Dispose();
                _trayIcon = null;
            }

            if (_miniPlayer != null)
            {
                try
                {
                    _miniPlayer.Close();
                    _miniPlayer.Dispose();
                }
                catch { }
                _miniPlayer = null;
            }

            if (_pythonProcess != null && !_pythonProcess.HasExited)
            {
                try
                {
                    _pythonProcess.Kill(true);
                }
                catch { }
                _pythonProcess = null;
            }
        }
    }
}
