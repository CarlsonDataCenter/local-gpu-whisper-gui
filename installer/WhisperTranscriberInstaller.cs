using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Windows.Forms;
using Microsoft.Win32;

internal static class WhisperTranscriberInstaller
{
    private static readonly byte[] PayloadMarker = Encoding.ASCII.GetBytes("CDC_WHISPER_INSTALLER_PAYLOAD_V1");
    private const string AppName = "Local GPU Whisper Transcriber";
    private const string PublisherName = "Carlson Data Center";
    private const string DisplayVersion = "1.0.0.0";
    private const string DefaultFolderName = "Local-GPU-Whisper";
    private const string InstallMarkerFile = ".local-gpu-whisper-install";
    private const string UninstallKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\CarlsonDataCenter.LocalGPUWhisper";
    private const string ProductUrl = "https://github.com/CarlsonDataCenter/local-gpu-whisper-gui";

    [STAThread]
    private static int Main(string[] args)
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        bool quiet = HasArg(args, "--quiet");
        bool noLaunch = HasArg(args, "--no-launch");
        bool verifyOnly = HasArg(args, "--verify");
        string installerPath = Process.GetCurrentProcess().MainModule.FileName;
        bool launchedAsUninstaller = string.Equals(Path.GetFileName(installerPath), "Uninstall.exe", StringComparison.OrdinalIgnoreCase);
        bool uninstall = HasArg(args, "--uninstall") || launchedAsUninstaller;
        string targetDir = GetArgValue(args, "--target");
        if (string.IsNullOrWhiteSpace(targetDir))
        {
            targetDir = uninstall
                ? Path.GetDirectoryName(installerPath)
                : DefaultInstallDir();
        }

        try
        {
            if (uninstall)
            {
                return RunUninstall(targetDir, quiet);
            }

            PayloadInfo payload = LocatePayload(installerPath);
            string appExe = Path.Combine(targetDir, "WhisperTranscriber.exe");

            InstallProgressForm progressForm = null;
            try
            {
                using (FileStream source = File.OpenRead(installerPath))
                using (SegmentStream payloadStream = new SegmentStream(source, payload.Offset, payload.Length))
                using (ZipArchive archive = new ZipArchive(payloadStream, ZipArchiveMode.Read))
                {
                    ValidateArchive(archive);
                    if (verifyOnly)
                    {
                        return 0;
                    }

                    if (!quiet && !ShowSetupDialog(ref targetDir))
                    {
                        return 0;
                    }

                    if (!quiet)
                    {
                        progressForm = new InstallProgressForm();
                        progressForm.Show();
                        progressForm.UpdateProgress(0, "Preparing installation...");
                    }

                    InstallArchive(archive, targetDir, progressForm);
                }

                string uninstallExe = WriteUninstallerStub(installerPath, payload.Offset, targetDir);

                if (progressForm != null)
                {
                    progressForm.UpdateProgress(96, "Creating shortcuts...");
                }
                CreateShortcuts(appExe, targetDir);

                if (progressForm != null)
                {
                    progressForm.UpdateProgress(98, "Registering uninstall entry...");
                }
                WriteUninstallEntry(appExe, uninstallExe, targetDir);

                if (progressForm != null)
                {
                    progressForm.UpdateProgress(100, "Installation complete.");
                    progressForm.Close();
                    progressForm = null;
                }
            }
            finally
            {
                if (progressForm != null)
                {
                    progressForm.Close();
                }
            }

            if (!quiet)
            {
                DialogResult launch = MessageBox.Show(
                    AppName + " was installed successfully.\n\nLaunch it now?",
                    AppName + " Setup",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Information);

                if (launch == DialogResult.Yes && File.Exists(appExe))
                {
                    Process.Start(appExe);
                }
            }
            else if (!noLaunch && File.Exists(appExe))
            {
                Process.Start(appExe);
            }

            return 0;
        }
        catch (Exception exc)
        {
            WriteFailureLog(exc);
            if (!quiet)
            {
                MessageBox.Show(
                    "Installation failed:\n\n" + exc,
                    AppName + " Setup",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }

            return 1;
        }
    }

    private static void WriteFailureLog(Exception exc)
    {
        try
        {
            string logPath = Path.Combine(Path.GetTempPath(), "LocalGPUWhisper-Installer.log");
            File.AppendAllText(
                logPath,
                "[" + DateTime.Now.ToString("u") + "] " + exc + Environment.NewLine + Environment.NewLine,
                Encoding.UTF8);
        }
        catch
        {
        }
    }

    private static string DefaultInstallDir()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            DefaultFolderName);
    }

    private static bool HasArg(string[] args, string name)
    {
        foreach (string arg in args)
        {
            if (string.Equals(arg, name, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }

        return false;
    }

    private static string GetArgValue(string[] args, string name)
    {
        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i];
            if (arg.StartsWith(name + "=", StringComparison.OrdinalIgnoreCase))
            {
                return arg.Substring(name.Length + 1).Trim('"');
            }

            if (string.Equals(arg, name, StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
            {
                return args[i + 1].Trim('"');
            }
        }

        return null;
    }

    private static bool ShowSetupDialog(ref string targetDir)
    {
        using (SetupForm form = new SetupForm(targetDir))
        {
            DialogResult result = form.ShowDialog();
            if (result != DialogResult.OK)
            {
                return false;
            }

            targetDir = form.InstallFolder;
            return true;
        }
    }

    private static PayloadInfo LocatePayload(string installerPath)
    {
        using (FileStream stream = File.OpenRead(installerPath))
        {
            if (stream.Length < PayloadMarker.Length + sizeof(long))
            {
                throw new InvalidOperationException("Installer payload is missing.");
            }

            stream.Seek(-sizeof(long), SeekOrigin.End);
            byte[] lengthBytes = ReadExact(stream, sizeof(long));
            long payloadLength = BitConverter.ToInt64(lengthBytes, 0);
            long markerOffset = stream.Length - sizeof(long) - PayloadMarker.Length;
            long payloadOffset = markerOffset - payloadLength;

            if (payloadLength <= 0 || payloadOffset < 0)
            {
                throw new InvalidOperationException("Installer payload length is invalid.");
            }

            stream.Seek(markerOffset, SeekOrigin.Begin);
            byte[] marker = ReadExact(stream, PayloadMarker.Length);
            for (int i = 0; i < PayloadMarker.Length; i++)
            {
                if (marker[i] != PayloadMarker[i])
                {
                    throw new InvalidOperationException("Installer payload marker is invalid.");
                }
            }

            return new PayloadInfo(payloadOffset, payloadLength);
        }
    }

    private static byte[] ReadExact(Stream stream, int length)
    {
        byte[] buffer = new byte[length];
        int offset = 0;
        while (offset < length)
        {
            int read = stream.Read(buffer, offset, length - offset);
            if (read == 0)
            {
                throw new EndOfStreamException();
            }

            offset += read;
        }

        return buffer;
    }

    private static void ValidateArchive(ZipArchive archive)
    {
        bool hasExe = false;
        bool hasCudaDll = false;

        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            string path = entry.FullName.Replace('\\', '/');
            if (string.Equals(path, "WhisperTranscriber.exe", StringComparison.OrdinalIgnoreCase))
            {
                hasExe = true;
            }

            if (path.StartsWith("_internal/nvidia/", StringComparison.OrdinalIgnoreCase)
                && path.EndsWith(".dll", StringComparison.OrdinalIgnoreCase))
            {
                hasCudaDll = true;
            }
        }

        if (!hasExe)
        {
            throw new InvalidOperationException("Payload does not contain WhisperTranscriber.exe.");
        }

        if (!hasCudaDll)
        {
            throw new InvalidOperationException("Payload does not contain bundled NVIDIA DLLs.");
        }
    }

    private static void InstallArchive(ZipArchive archive, string targetDir, InstallProgressForm progressForm)
    {
        string fullTarget = Path.GetFullPath(targetDir);
        string targetPrefix = fullTarget.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            + Path.DirectorySeparatorChar;

        Directory.CreateDirectory(fullTarget);
        if (progressForm != null)
        {
            progressForm.UpdateProgress(2, "Removing previous app files...");
        }
        DeleteKnownInstallFiles(fullTarget);

        List<ZipArchiveEntry> entries = new List<ZipArchiveEntry>();
        foreach (ZipArchiveEntry entry in archive.Entries)
        {
            entries.Add(entry);
        }

        int processed = 0;
        foreach (ZipArchiveEntry entry in entries)
        {
            string relativeName = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
            string destination = Path.GetFullPath(Path.Combine(fullTarget, relativeName));
            if (!destination.StartsWith(targetPrefix, StringComparison.OrdinalIgnoreCase)
                && !string.Equals(destination, fullTarget, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Payload contains an unsafe path: " + entry.FullName);
            }

            if (string.IsNullOrEmpty(entry.Name))
            {
                Directory.CreateDirectory(destination);
                processed++;
                continue;
            }

            string destinationDir = Path.GetDirectoryName(destination);
            if (!Directory.Exists(destinationDir))
            {
                Directory.CreateDirectory(destinationDir);
            }

            using (Stream input = entry.Open())
            using (FileStream output = File.Create(destination))
            {
                input.CopyTo(output);
            }

            processed++;
            if (progressForm != null && (processed == 1 || processed % 25 == 0 || processed == entries.Count))
            {
                int percent = 5 + (int)Math.Round((processed / Math.Max(1.0, entries.Count)) * 90.0);
                progressForm.UpdateProgress(percent, "Installing " + entry.FullName);
            }
        }

        File.WriteAllText(
            Path.Combine(fullTarget, InstallMarkerFile),
            AppName + Environment.NewLine + DateTime.Now.ToString("u"),
            Encoding.UTF8);
    }

    private static string WriteUninstallerStub(string installerPath, long stubLength, string targetDir)
    {
        string uninstallExe = Path.Combine(targetDir, "Uninstall.exe");
        using (FileStream input = File.OpenRead(installerPath))
        using (FileStream output = File.Create(uninstallExe))
        {
            CopyBytes(input, output, stubLength);
        }

        return uninstallExe;
    }

    private static void CopyBytes(Stream input, Stream output, long bytesToCopy)
    {
        byte[] buffer = new byte[1024 * 1024];
        long remaining = bytesToCopy;
        while (remaining > 0)
        {
            int toRead = (int)Math.Min(buffer.Length, remaining);
            int read = input.Read(buffer, 0, toRead);
            if (read == 0)
            {
                throw new EndOfStreamException();
            }

            output.Write(buffer, 0, read);
            remaining -= read;
        }
    }

    private static void CreateShortcuts(string appExe, string targetDir)
    {
        string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        CreateShortcut(Path.Combine(desktop, AppName + ".lnk"), appExe, targetDir);

        string startMenuFolder = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.Programs),
            PublisherName);
        if (TryCreateDirectory(startMenuFolder))
        {
            CreateShortcut(Path.Combine(startMenuFolder, AppName + ".lnk"), appExe, targetDir);
        }
    }

    private static void RemoveShortcuts()
    {
        TryDeleteFile(Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
            AppName + ".lnk"));

        string startMenuFolder = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.Programs),
            PublisherName);
        TryDeleteFile(Path.Combine(startMenuFolder, AppName + ".lnk"));

        if (Directory.Exists(startMenuFolder) && Directory.GetFileSystemEntries(startMenuFolder).Length == 0)
        {
            TryDeleteDirectory(startMenuFolder, false);
        }
    }

    private static void WriteUninstallEntry(string appExe, string uninstallExe, string targetDir)
    {
        try
        {
            WriteUninstallEntryToRoot(Registry.LocalMachine, appExe, uninstallExe, targetDir);
            Registry.CurrentUser.DeleteSubKeyTree(UninstallKeyPath, false);
            return;
        }
        catch (UnauthorizedAccessException)
        {
        }
        catch (System.Security.SecurityException)
        {
        }

        WriteUninstallEntryToRoot(Registry.CurrentUser, appExe, uninstallExe, targetDir);
    }

    private static void WriteUninstallEntryToRoot(RegistryKey root, string appExe, string uninstallExe, string targetDir)
    {
        using (RegistryKey key = root.CreateSubKey(UninstallKeyPath))
        {
            if (key == null)
            {
                return;
            }

            key.SetValue("DisplayName", AppName, RegistryValueKind.String);
            key.SetValue("DisplayVersion", DisplayVersion, RegistryValueKind.String);
            key.SetValue("Publisher", PublisherName, RegistryValueKind.String);
            key.SetValue("InstallLocation", targetDir, RegistryValueKind.String);
            key.SetValue("InstallSource", Path.GetDirectoryName(uninstallExe), RegistryValueKind.String);
            key.SetValue("DisplayIcon", appExe + ",0", RegistryValueKind.String);
            key.SetValue("UninstallString", Quote(uninstallExe) + " --uninstall --target " + Quote(targetDir), RegistryValueKind.String);
            key.SetValue("QuietUninstallString", Quote(uninstallExe) + " --uninstall --quiet --target " + Quote(targetDir), RegistryValueKind.String);
            key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"), RegistryValueKind.String);
            key.SetValue("Contact", PublisherName, RegistryValueKind.String);
            key.SetValue("URLInfoAbout", ProductUrl, RegistryValueKind.String);
            key.SetValue("HelpLink", ProductUrl, RegistryValueKind.String);
            key.SetValue("Comments", "Local GPU Whisper transcription with bundled NVIDIA runtime DLLs.", RegistryValueKind.String);
            key.SetValue("NoModify", 1, RegistryValueKind.DWord);
            key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            key.SetValue("SystemComponent", 0, RegistryValueKind.DWord);
            key.SetValue("WindowsInstaller", 0, RegistryValueKind.DWord);
            key.SetValue("EstimatedSize", EstimateSizeKb(targetDir), RegistryValueKind.DWord);
        }
    }

    private static int RunUninstall(string targetDir, bool quiet)
    {
        if (!quiet)
        {
            DialogResult result = MessageBox.Show(
                "Remove " + AppName + " from this computer?",
                AppName + " Uninstall",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Question);

            if (result != DialogResult.Yes)
            {
                return 0;
            }
        }

        RemoveShortcuts();
        Registry.CurrentUser.DeleteSubKeyTree(UninstallKeyPath, false);
        try
        {
            Registry.LocalMachine.DeleteSubKeyTree(UninstallKeyPath, false);
        }
        catch (UnauthorizedAccessException)
        {
        }
        catch (System.Security.SecurityException)
        {
        }

        string fullTarget = Path.GetFullPath(targetDir);
        string currentExe = Process.GetCurrentProcess().MainModule.FileName;
        if (IsPathInside(currentExe, fullTarget))
        {
            ScheduleKnownInstallRemoval(fullTarget);
        }
        else
        {
            DeleteKnownInstallFiles(fullTarget);
            TryDeleteFile(Path.Combine(fullTarget, InstallMarkerFile));
            TryRemoveDirectoryIfEmpty(fullTarget);
        }

        if (!quiet)
        {
            MessageBox.Show(
                AppName + " was uninstalled.",
                AppName + " Uninstall",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
        }

        return 0;
    }

    private static bool IsPathInside(string path, string directory)
    {
        string fullPath = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        string fullDirectory = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return fullPath.Equals(fullDirectory, StringComparison.OrdinalIgnoreCase)
            || fullPath.StartsWith(fullDirectory + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
    }

    private static void ScheduleKnownInstallRemoval(string targetDir)
    {
        string command = "/c timeout /t 2 /nobreak > nul"
            + " & del /f /q " + Quote(Path.Combine(targetDir, "WhisperTranscriber.exe"))
            + " & del /f /q " + Quote(Path.Combine(targetDir, "Uninstall.exe"))
            + " & del /f /q " + Quote(Path.Combine(targetDir, InstallMarkerFile))
            + " & rmdir /s /q " + Quote(Path.Combine(targetDir, "_internal"))
            + " & rmdir /q " + Quote(targetDir);
        ProcessStartInfo info = new ProcessStartInfo("cmd.exe", command)
        {
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        Process.Start(info);
    }

    private static void DeleteKnownInstallFiles(string targetDir)
    {
        TryDeleteFile(Path.Combine(targetDir, "WhisperTranscriber.exe"));
        TryDeleteFile(Path.Combine(targetDir, "Uninstall.exe"));
        TryDeleteFile(Path.Combine(targetDir, InstallMarkerFile));

        string internalDir = Path.Combine(targetDir, "_internal");
        if (Directory.Exists(internalDir))
        {
            TryDeleteDirectory(internalDir, true);
        }
    }

    private static void TryRemoveDirectoryIfEmpty(string path)
    {
        if (Directory.Exists(path) && Directory.GetFileSystemEntries(path).Length == 0)
        {
            TryDeleteDirectory(path, false);
        }
    }

    private static int EstimateSizeKb(string targetDir)
    {
        long total = 0;
        if (Directory.Exists(targetDir))
        {
            foreach (string file in Directory.GetFiles(targetDir, "*", SearchOption.AllDirectories))
            {
                try
                {
                    total += new FileInfo(file).Length;
                }
                catch (IOException)
                {
                }
                catch (UnauthorizedAccessException)
                {
                }
            }
        }

        long kb = Math.Max(1, total / 1024);
        return kb > int.MaxValue ? int.MaxValue : (int)kb;
    }

    private static void TryDeleteFile(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    private static void TryDeleteDirectory(string path, bool recursive)
    {
        try
        {
            if (Directory.Exists(path))
            {
                Directory.Delete(path, recursive);
            }
        }
        catch (IOException)
        {
        }
        catch (UnauthorizedAccessException)
        {
        }
    }

    private static bool TryCreateDirectory(string path)
    {
        try
        {
            Directory.CreateDirectory(path);
            return true;
        }
        catch (IOException)
        {
            return false;
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
    }

    private static void CreateShortcut(string shortcutPath, string targetPath, string workingDirectory)
    {
        try
        {
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            if (shellType == null)
            {
                return;
            }

            object shell = Activator.CreateInstance(shellType);
            object shortcut = shellType.InvokeMember(
                "CreateShortcut",
                BindingFlags.InvokeMethod,
                null,
                shell,
                new object[] { shortcutPath });

            Type shortcutType = shortcut.GetType();
            shortcutType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetPath });
            shortcutType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workingDirectory });
            shortcutType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { targetPath });
            shortcutType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { AppName });
            shortcutType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
        }
        catch
        {
        }
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }

    private sealed class PayloadInfo
    {
        public PayloadInfo(long offset, long length)
        {
            Offset = offset;
            Length = length;
        }

        public long Offset { get; private set; }
        public long Length { get; private set; }
    }

    private sealed class InstallProgressForm : Form
    {
        private readonly ProgressBar progressBar;
        private readonly Label statusLabel;

        public InstallProgressForm()
        {
            Text = AppName + " Setup";
            Width = 520;
            Height = 210;
            MinimizeBox = false;
            MaximizeBox = false;
            ControlBox = false;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;

            Icon extractedIcon = Icon.ExtractAssociatedIcon(Process.GetCurrentProcess().MainModule.FileName);
            if (extractedIcon != null)
            {
                Icon = extractedIcon;
            }

            Panel header = new Panel
            {
                Dock = DockStyle.Top,
                Height = 70,
                BackColor = Color.FromArgb(5, 18, 36)
            };
            Controls.Add(header);

            Label title = new Label
            {
                Left = 22,
                Top = 18,
                Width = 470,
                Height = 28,
                Text = "Installing " + AppName,
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 13, FontStyle.Bold)
            };
            header.Controls.Add(title);

            statusLabel = new Label
            {
                Left = 22,
                Top = 94,
                Width = 460,
                Height = 24,
                Text = "Preparing installation...",
                Font = new Font("Segoe UI", 9)
            };
            Controls.Add(statusLabel);

            progressBar = new ProgressBar
            {
                Left = 22,
                Top = 126,
                Width = 460,
                Height = 24,
                Minimum = 0,
                Maximum = 100,
                Style = ProgressBarStyle.Continuous
            };
            Controls.Add(progressBar);
        }

        public void UpdateProgress(int percent, string status)
        {
            int safePercent = Math.Max(progressBar.Minimum, Math.Min(progressBar.Maximum, percent));
            progressBar.Value = safePercent;
            statusLabel.Text = TrimStatus(status);
            Refresh();
            Application.DoEvents();
        }

        private static string TrimStatus(string status)
        {
            if (string.IsNullOrWhiteSpace(status))
            {
                return "Installing...";
            }

            return status.Length > 82 ? status.Substring(0, 79) + "..." : status;
        }
    }

    private sealed class SetupForm : Form
    {
        private readonly TextBox folderTextBox;

        public SetupForm(string initialFolder)
        {
            InstallFolder = initialFolder;
            Text = AppName + " Setup";
            Width = 620;
            Height = 330;
            MinimizeBox = false;
            MaximizeBox = false;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterScreen;
            Icon extractedIcon = Icon.ExtractAssociatedIcon(Process.GetCurrentProcess().MainModule.FileName);
            if (extractedIcon != null)
            {
                Icon = extractedIcon;
            }

            Panel header = new Panel
            {
                Dock = DockStyle.Top,
                Height = 92,
                BackColor = Color.FromArgb(5, 18, 36)
            };
            Controls.Add(header);

            PictureBox iconBox = new PictureBox
            {
                Left = 24,
                Top = 18,
                Width = 56,
                Height = 56,
                SizeMode = PictureBoxSizeMode.StretchImage,
                Image = Icon != null ? Icon.ToBitmap() : null
            };
            header.Controls.Add(iconBox);

            Label title = new Label
            {
                Left = 96,
                Top = 18,
                Width = 480,
                Height = 28,
                Text = "Install " + AppName,
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 14, FontStyle.Bold)
            };
            header.Controls.Add(title);

            Label subtitle = new Label
            {
                Left = 96,
                Top = 50,
                Width = 480,
                Height = 28,
                Text = "Local GPU Whisper transcription with bundled NVIDIA runtime DLLs.",
                ForeColor = Color.FromArgb(202, 218, 235),
                Font = new Font("Segoe UI", 9)
            };
            header.Controls.Add(subtitle);

            Label folderLabel = new Label
            {
                Left = 24,
                Top = 120,
                Width = 550,
                Height = 24,
                Text = "Install folder",
                Font = new Font("Segoe UI", 9, FontStyle.Bold)
            };
            Controls.Add(folderLabel);

            folderTextBox = new TextBox
            {
                Left = 24,
                Top = 150,
                Width = 455,
                Height = 26,
                Text = initialFolder
            };
            Controls.Add(folderTextBox);

            Button browseButton = new Button
            {
                Left = 492,
                Top = 148,
                Width = 88,
                Height = 30,
                Text = "Browse..."
            };
            browseButton.Click += BrowseButton_Click;
            Controls.Add(browseButton);

            Label note = new Label
            {
                Left = 24,
                Top = 190,
                Width = 555,
                Height = 44,
                Text = "The default location is your local app data folder. Choose another folder if you want the app installed elsewhere.",
                ForeColor = Color.FromArgb(80, 80, 80),
                Font = new Font("Segoe UI", 9)
            };
            Controls.Add(note);

            Button cancelButton = new Button
            {
                Left = 390,
                Top = 246,
                Width = 88,
                Height = 32,
                Text = "Cancel",
                DialogResult = DialogResult.Cancel
            };
            Controls.Add(cancelButton);

            Button installButton = new Button
            {
                Left = 492,
                Top = 246,
                Width = 88,
                Height = 32,
                Text = "Install",
                DialogResult = DialogResult.OK
            };
            installButton.Click += InstallButton_Click;
            Controls.Add(installButton);

            AcceptButton = installButton;
            CancelButton = cancelButton;
        }

        public string InstallFolder { get; private set; }

        private void BrowseButton_Click(object sender, EventArgs e)
        {
            using (FolderBrowserDialog dialog = new FolderBrowserDialog())
            {
                dialog.Description = "Choose where to install " + AppName;
                dialog.SelectedPath = folderTextBox.Text;
                dialog.ShowNewFolderButton = true;
                if (dialog.ShowDialog(this) == DialogResult.OK)
                {
                    folderTextBox.Text = dialog.SelectedPath;
                }
            }
        }

        private void InstallButton_Click(object sender, EventArgs e)
        {
            string folder = folderTextBox.Text.Trim();
            if (string.IsNullOrWhiteSpace(folder))
            {
                MessageBox.Show(this, "Please choose an install folder.", AppName + " Setup", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                DialogResult = DialogResult.None;
                return;
            }

            try
            {
                InstallFolder = Path.GetFullPath(Environment.ExpandEnvironmentVariables(folder));
                if (Directory.Exists(InstallFolder)
                    && Directory.GetFileSystemEntries(InstallFolder).Length > 0
                    && !File.Exists(Path.Combine(InstallFolder, InstallMarkerFile))
                    && !File.Exists(Path.Combine(InstallFolder, "WhisperTranscriber.exe")))
                {
                    DialogResult overwrite = MessageBox.Show(
                        this,
                        "The selected folder already contains files. The installer will only replace Local GPU Whisper files, but using a dedicated folder is recommended.\n\nContinue with this folder?",
                        AppName + " Setup",
                        MessageBoxButtons.YesNo,
                        MessageBoxIcon.Warning);
                    if (overwrite != DialogResult.Yes)
                    {
                        DialogResult = DialogResult.None;
                    }
                }
            }
            catch (Exception exc)
            {
                MessageBox.Show(this, "The install folder is not valid:\n\n" + exc.Message, AppName + " Setup", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                DialogResult = DialogResult.None;
            }
        }
    }

    private sealed class SegmentStream : Stream
    {
        private readonly Stream inner;
        private readonly long offset;
        private readonly long length;
        private long position;

        public SegmentStream(Stream inner, long offset, long length)
        {
            this.inner = inner;
            this.offset = offset;
            this.length = length;
            this.position = 0;
        }

        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return true; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { return length; } }

        public override long Position
        {
            get { return position; }
            set { Seek(value, SeekOrigin.Begin); }
        }

        public override void Flush()
        {
        }

        public override int Read(byte[] buffer, int offsetInBuffer, int count)
        {
            if (position >= length)
            {
                return 0;
            }

            long remaining = length - position;
            if (count > remaining)
            {
                count = (int)Math.Min(count, remaining);
            }

            inner.Seek(offset + position, SeekOrigin.Begin);
            int read = inner.Read(buffer, offsetInBuffer, count);
            position += read;
            return read;
        }

        public override long Seek(long seekOffset, SeekOrigin origin)
        {
            long nextPosition;
            if (origin == SeekOrigin.Begin)
            {
                nextPosition = seekOffset;
            }
            else if (origin == SeekOrigin.Current)
            {
                nextPosition = position + seekOffset;
            }
            else
            {
                nextPosition = length + seekOffset;
            }

            if (nextPosition < 0)
            {
                throw new IOException("Cannot seek before the start of the payload.");
            }

            position = nextPosition;
            return position;
        }

        public override void SetLength(long value)
        {
            throw new NotSupportedException();
        }

        public override void Write(byte[] buffer, int offsetInBuffer, int count)
        {
            throw new NotSupportedException();
        }
    }
}
