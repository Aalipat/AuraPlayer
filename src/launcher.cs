using System;
using System.IO;
using System.Diagnostics;
using System.Windows.Forms;

namespace YouTubeDownloaderLauncher
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            try
            {
                string baseDir = AppDomain.CurrentDomain.BaseDirectory;
                string launcherPy = Path.Combine(baseDir, "launcher.py");
                string venvPython = Path.Combine(baseDir, "venv", "Scripts", "python.exe");

                if (!File.Exists(launcherPy))
                {
                    MessageBox.Show(
                        "فایل launcher.py در کنار برنامه پیدا نشد!\nlauncher.py was not found in:\n" + baseDir,
                        "YouTube & Spotify Downloader",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return;
                }

                ProcessStartInfo psi = new ProcessStartInfo();
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = true;

                if (File.Exists(venvPython))
                {
                    psi.FileName = venvPython;
                    psi.Arguments = "\"" + launcherPy + "\"";
                }
                else
                {
                    psi.FileName = "cmd.exe";
                    psi.Arguments = "/c py -3 \"" + launcherPy + "\" || python \"" + launcherPy + "\"";
                }

                Process.Start(psi);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "خطا در اجرای لانچر:\n" + ex.Message,
                    "خطا / Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }
        }
    }
}
