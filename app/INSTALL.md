# Installing the Butler desktop app

The app is a window, a tray icon and a launcher for Butler. It does not
contain Butler: it needs a Butler checkout on the same computer.

1. Copy `Butler_0.1.0_x64-setup.exe` to the computer (USB stick, shared
   folder or any file transfer) and double-click it. No administrator
   password is asked: it installs for your Windows user only, under
   `%LOCALAPPDATA%\Butler`.

2. Windows shows "Windows protected your PC" (SmartScreen). The installer is
   not code-signed, by the owner's choice, so this is expected. Click
   "More info", then "Run anyway".

3. If WebView2 is missing (rare on Windows 11), the installer downloads it
   by itself; the computer needs internet for that one step.

4. Start Butler from the Start menu. The first launch only asks "Where is
   Butler?" when it cannot find a checkout in the usual places
   (`%USERPROFILE%\Butler`, `C:\PROJECTS\butler`, `E:\PROJECTS\butler`).
   Pick the folder that contains `daemon\butlerd.py`. The answer is kept in
   `%APPDATA%\Butler\app.json`.

5. Closing the window hides it to the tray; Butler keeps running. Use the
   tray icon's menu to open it again, to start with Windows, or to Quit
   (Quit stops Butler).

6. Windows' privacy switch "Let desktop apps access your microphone" must
   be on for voice to work; the app tells you if it is off.

To uninstall: Settings, Apps, Butler, Uninstall. This removes only the app.
Your Butler data folder and the Butler checkout are not touched.
