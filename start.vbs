' Start with no console window at all (tray icon only). Double-click = all that are set up;
' a shortcut with an argument (start.vbs wechat) starts just that one.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
d = fso.GetParentFolderName(WScript.ScriptFullName)
t = "all"
If WScript.Arguments.Count > 0 Then t = WScript.Arguments(0)
sh.CurrentDirectory = d
sh.Run "cmd /c call """ & d & "\start.bat"" " & t, 0, False
