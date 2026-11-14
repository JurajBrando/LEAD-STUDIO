' Starts Lead Studio with no console window at all - it just appears as its own program.
' Right-click this file and "Send to > Desktop (create shortcut)" to launch it from the desktop.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
sh.Run "cmd /c python """ & here & "\lead_studio.py""", 0, False
