using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

namespace YouTubeDownloaderApp;

static class Program
{
    private const string AppMutexName = "Global\\YouTube_Spotify_Downloader_SingleInstance_Mutex";

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    private static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

    private const int SW_RESTORE = 9;

    [STAThread]
    static void Main()
    {
        // Single Instance Enforcement via Windows Named Mutex
        using var mutex = new Mutex(true, AppMutexName, out bool isFirstInstance);
        if (!isFirstInstance)
        {
            // Activate existing instance: first try waking up via named EventWaitHandle
            try
            {
                using var wakeupEvent = EventWaitHandle.OpenExisting(MainForm.WakeupEventName);
                wakeupEvent.Set();
            }
            catch
            {
                // Fallback to Window Handle if event wait handle is not ready
                try
                {
                    Process current = Process.GetCurrentProcess();
                    foreach (Process process in Process.GetProcessesByName(current.ProcessName))
                    {
                        if (process.Id != current.Id && process.MainWindowHandle != IntPtr.Zero)
                        {
                            ShowWindowAsync(process.MainWindowHandle, SW_RESTORE);
                            SetForegroundWindow(process.MainWindowHandle);
                            break;
                        }
                    }
                }
                catch { }
            }
            return;
        }

        ApplicationConfiguration.Initialize();
        Application.Run(new MainForm());
    }
}