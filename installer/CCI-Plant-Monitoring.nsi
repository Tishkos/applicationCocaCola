; CCI Plant Monitoring - the Windows Setup (NSIS 3). Built by installer/build.sh (or the release workflow):
;   installer/build_runtime.py <version>   (build/stage: the program with its own Python and every package)
;   makensis -DVERSION=1.2.3 installer/CCI-Plant-Monitoring.nsi   ->  dist/CCI-Plant-Monitoring-Setup-1.2.3.exe
;
; Standalone: the Setup carries everything (Python, packages, the program, CCI Plant Monitoring.exe and
; CCI Plant Monitoring Service.exe). Nothing is downloaded and nothing else needs to be on the PC.
; The wizard: Welcome, folder, options (automatic updates, firewall, Turkey's Modbus feed, Desktop shortcut, the window
; at sign-in, an earlier copy to bring over, this PC's plant address), progress, finish ("Open CCI Plant Monitoring").
; scripts\install.ps1 registers the background service, the updater, the firewall rules and the shortcuts;
; on uninstall, scripts\uninstall.ps1 removes them. Settings and data live in C:\ProgramData\CCI Plant Monitoring and are kept
; by an update; the uninstaller deletes them only when asked ("Collected data and settings").
;
; Command line:
;   /S                silent (the updater uses /S /UPDATE: the earlier choices, no pages)
;   /UPDATE           keep every earlier choice, skip the options
;   /AUTOUPDATE=1|0  /FIREWALL=1|0  /DESKTOP=1|0  /TURKEY=1|0  /STARTWINDOW=1|0  /IMPORT=<folder>  /SOURCE=<this PC's plant IP>
;   (/TURKEY switches Turkey's Modbus feed on or off in the settings; /STARTWINDOW opens the window at sign-in;
;    left out on an update, both stay as they are)
;   uninstall.exe /S [/DELETEDATA]

Unicode true
Target amd64-unicode
ManifestDPIAware true
SetCompressor /SOLID lzma
RequestExecutionLevel admin

!ifndef VERSION
  !define VERSION "1.0.0"
!endif
!define APPNAME   "CCI Plant Monitoring"
!define PUBLISHER "Tishko"
!define REGKEY    "Software\CCI Plant Monitoring"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\CCIPlantMonitoring"
!define PS        '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass'
!define SRC       ".."
!define STAGE     "..\build\stage"

Name "${APPNAME}"
OutFile "${SRC}\dist\CCI-Plant-Monitoring-Setup-${VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APPNAME}"
BrandingText "${APPNAME} ${VERSION}"
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName" "${APPNAME}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "${APPNAME} Setup"
VIAddVersionKey "CompanyName" "${PUBLISHER}"
VIAddVersionKey "LegalCopyright" "${PUBLISHER}"

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "Sections.nsh"

Var AutoUpd     ; 1 | 0
Var Fw          ; 1 | 0
Var Desk        ; 1 | 0
Var Turkey      ; 1 | 0 | "" (not chosen: the settings stay as they are)
Var StartWin    ; 1 | 0 | "" (not chosen: as it is)
Var ImportDir
Var SrcAddr
Var IsUpdate    ; 1 = /UPDATE
Var Installed   ; 1 = an earlier install of this Setup is on the PC
Var FinishText
Var hAuto
Var hFw
Var hDesk
Var hTurkey
Var hStartWin
Var hImport
Var hSrc

; ---- look ------------------------------------------------------------------------------------------------------
!define MUI_ICON "${SRC}\app\PlantCollector.ico"
!define MUI_UNICON "${SRC}\app\PlantCollector.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "wizard.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "wizard.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "header.bmp"
!define MUI_HEADERIMAGE_UNBITMAP "header.bmp"
!define MUI_ABORTWARNING

; ---- the installer's pages ---------------------------------------------------------------------------------------
!define MUI_WELCOMEPAGE_TITLE "${APPNAME} ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "This installs the plant monitoring application on this PC:$\r$\n$\r$\n\
- the background service: reads the plant devices (read-only), keeps every reading in its database and feeds Turkey. It starts with Windows and keeps running when the window is closed.$\r$\n$\r$\n\
- the ${APPNAME} window (Start Menu and Desktop): the live dashboard, no browser needed.$\r$\n$\r$\n\
- the updater: new versions from GitHub, installed at night.$\r$\n$\r$\n\
Everything is included: no Python, no internet and no other program is needed. Settings and history are kept when you update."
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfUpdate
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfInstalled
!insertmacro MUI_PAGE_DIRECTORY
Page custom OptionsPage OptionsLeave
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TITLE "${APPNAME} is ready"
!define MUI_FINISHPAGE_TEXT "$FinishText"
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Open ${APPNAME}"
!define MUI_FINISHPAGE_RUN_FUNCTION OpenApp
!insertmacro MUI_PAGE_FINISH

; ---- the uninstaller's pages -------------------------------------------------------------------------------------
!insertmacro MUI_UNPAGE_CONFIRM
!define MUI_COMPONENTSPAGE_TEXT_TOP "The program, its background service, the updater, the firewall rules and the shortcuts are removed. Tick the second line to delete the collected history and the settings as well (otherwise a later Setup carries on with them)."
!insertmacro MUI_UNPAGE_COMPONENTS
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ---- helpers ------------------------------------------------------------------------------------------------------
!macro TrimSlash var
  StrCpy $R9 ${var} 1 -1
  ${If} $R9 == "\"
    StrCpy ${var} ${var} -1
  ${EndIf}
!macroend

!macro ReadChoice name var
  ReadRegStr $R8 HKLM "${REGKEY}" "${name}"
  ${If} $R8 != ""
    StrCpy ${var} $R8
  ${EndIf}
!macroend

!macro OptArg opt var
  ClearErrors
  ${GetOptions} $R0 "${opt}" $R1
  ${IfNot} ${Errors}
    StrCpy ${var} $R1
  ${EndIf}
!macroend

Function .onInit
  SetRegView 64
  SetShellVarContext all
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${APPNAME} needs 64-bit Windows 10 or 11." /SD IDOK
    Abort
  ${EndIf}
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Global\CCIPlantMonitoringSetup") p .r1 ?e'
  Pop $R0
  ${If} $R0 == 183
    MessageBox MB_ICONEXCLAMATION "The ${APPNAME} Setup is already running." /SD IDOK
    Abort
  ${EndIf}

  StrCpy $AutoUpd "1"
  StrCpy $Fw "1"
  StrCpy $Desk "1"
  StrCpy $Turkey ""
  StrCpy $StartWin ""
  StrCpy $ImportDir ""
  StrCpy $SrcAddr ""
  StrCpy $IsUpdate "0"
  StrCpy $Installed "0"
  ; an earlier install: its folder and its choices
  ReadRegStr $R8 HKLM "${REGKEY}" "InstallDir"
  ${If} $R8 != ""
  ${AndIf} ${FileExists} "$R8\scripts\install.ps1"
    StrCpy $INSTDIR $R8
    StrCpy $Installed "1"
  ${EndIf}
  !insertmacro ReadChoice "AutoUpdate" $AutoUpd
  !insertmacro ReadChoice "Firewall" $Fw
  !insertmacro ReadChoice "Desktop" $Desk
  !insertmacro ReadChoice "Turkey" $Turkey
  !insertmacro ReadChoice "StartWindow" $StartWin
  Call ReadTurkeyFromSettings                      ; the settings file wins over the remembered box (turkey on|off by hand)
  !insertmacro ReadChoice "SourceAddress" $SrcAddr
  ; the command line wins
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/UPDATE" $R1
  ${IfNot} ${Errors}
    StrCpy $IsUpdate "1"
  ${EndIf}
  !insertmacro OptArg "/AUTOUPDATE=" $AutoUpd
  !insertmacro OptArg "/FIREWALL=" $Fw
  !insertmacro OptArg "/DESKTOP=" $Desk
  !insertmacro OptArg "/TURKEY=" $Turkey
  !insertmacro OptArg "/STARTWINDOW=" $StartWin
  !insertmacro OptArg "/IMPORT=" $ImportDir
  !insertmacro OptArg "/SOURCE=" $SrcAddr
FunctionEnd

Function ReadTurkeyFromSettings
  ; The box for Turkey's feed starts as the settings file of an earlier install says (publish.modbus_tcp.enabled), so
  ; running this Setup again, or an update, never switches the feed by surprise: the file wins over the box remembered
  ; in the registry (the feed may have been switched by hand since, with `turkey on|off`). No file, or no plain
  ; "enabled: true/false" line in the modbus_tcp block: $Turkey stays as it was ("" on a new PC: the box starts ticked).
  ReadEnvStr $R0 PROGRAMDATA
  StrCpy $R0 "$R0\${APPNAME}\config\site.plant.yaml"
  ${IfNot} ${FileExists} $R0
    Return
  ${EndIf}
  ClearErrors
  FileOpen $R1 $R0 r
  ${If} ${Errors}
    Return
  ${EndIf}
  StrCpy $R2 "0"                                   ; 1 = the lines after "  modbus_tcp:"
  ${Do}
    FileRead $R1 $R3
    ${If} ${Errors}
      ${Break}
    ${EndIf}
    StrCpy $R4 $R3 13
    ${If} $R4 == "  modbus_tcp:"
      StrCpy $R2 "1"
    ${ElseIf} $R2 == "1"
      StrCpy $R4 $R3 17
      ${If} $R4 == "    enabled: true"
        StrCpy $Turkey "1"
        ${Break}
      ${EndIf}
      StrCpy $R4 $R3 18
      ${If} $R4 == "    enabled: false"
        StrCpy $Turkey "0"
        ${Break}
      ${EndIf}
      StrCpy $R4 $R3 2
      ${If} $R4 != "  "                            ; the block ended without a switch
        ${Break}
      ${EndIf}
    ${EndIf}
  ${Loop}
  FileClose $R1
FunctionEnd

Function .onVerifyInstDir
  ; never a drive's root (C:\) or a too short path: the uninstaller removes the folder's contents
  StrLen $R0 "$INSTDIR"
  ${If} $R0 < 4
    Abort
  ${EndIf}
FunctionEnd

Function SkipIfUpdate
  ${If} $IsUpdate == "1"
    Abort
  ${EndIf}
FunctionEnd

Function SkipIfInstalled
  ; an update goes into the folder of the earlier install (to move it: uninstall first, keeping the data)
  ${If} $Installed == "1"
    Abort
  ${EndIf}
FunctionEnd

Function OptionsPage
  ${If} $IsUpdate == "1"
    Abort
  ${EndIf}
  !insertmacro MUI_HEADER_TEXT "Options" "Updates, firewall, Turkey's feed, the window and an earlier copy."
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 16u "The service reads the plant (read-only), stores every reading and can serve Turkey's Modbus feed. Choose:"
  Pop $0
  ${NSD_CreateCheckBox} 0 17u 100% 11u "Automatic updates from GitHub (installed at night, 01:00-05:00)"
  Pop $hAuto
  ${If} $AutoUpd == "1"
    ${NSD_Check} $hAuto
  ${EndIf}
  ${NSD_CreateCheckBox} 0 29u 100% 11u "Firewall: allow Turkey's Modbus (TCP 502) and the dashboard (TCP 8080)"
  Pop $hFw
  ${If} $Fw == "1"
    ${NSD_Check} $hFw
  ${EndIf}
  ${NSD_CreateCheckBox} 0 41u 100% 11u "Turkey's Modbus feed: serve the plant's readings on TCP 502 (switched on in the settings)"
  Pop $hTurkey
  ${If} $Turkey != "0"
    ${NSD_Check} $hTurkey
  ${EndIf}
  ${NSD_CreateCheckBox} 0 53u 100% 11u "Open the ${APPNAME} window when a user signs in to Windows"
  Pop $hStartWin
  ${If} $StartWin != "0"
    ${NSD_Check} $hStartWin
  ${EndIf}
  ${NSD_CreateCheckBox} 0 65u 100% 11u "Shortcut on the Desktop (the Start Menu always gets one)"
  Pop $hDesk
  ${If} $Desk == "1"
    ${NSD_Check} $hDesk
  ${EndIf}

  ${NSD_CreateLabel} 0 80u 100% 10u "Optional: an earlier copy to bring settings and history from (one that ran as the Windows task is found by itself)"
  Pop $0
  ${NSD_CreateDirRequest} 0 91u 78% 12u "$ImportDir"
  Pop $hImport
  ${NSD_CreateBrowseButton} 80% 90u 20% 14u "Browse..."
  Pop $0
  ${NSD_OnClick} $0 BrowseImport

  ${NSD_CreateLabel} 0 108u 64% 18u "This PC's plant address, only if two of its adapters are in the plant's range (empty: automatic)"
  Pop $0
  ${NSD_CreateText} 66% 110u 34% 12u "$SrcAddr"
  Pop $hSrc
  ${If} $Installed == "1"
    ${NSD_CreateLabel} 0 128u 100% 10u "Installed in $INSTDIR: updated there; settings and history stay."
    Pop $0
  ${EndIf}
  nsDialogs::Show
FunctionEnd

Function BrowseImport
  nsDialogs::SelectFolderDialog "The folder of the earlier copy (the one with the config and data folders)" "$ImportDir"
  Pop $0
  ${If} $0 != error
    ${NSD_SetText} $hImport $0
  ${EndIf}
FunctionEnd

Function OptionsLeave
  ${NSD_GetState} $hAuto $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $AutoUpd "1"
  ${Else}
    StrCpy $AutoUpd "0"
  ${EndIf}
  ${NSD_GetState} $hFw $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $Fw "1"
  ${Else}
    StrCpy $Fw "0"
  ${EndIf}
  ${NSD_GetState} $hDesk $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $Desk "1"
  ${Else}
    StrCpy $Desk "0"
  ${EndIf}
  ${NSD_GetState} $hTurkey $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $Turkey "1"
  ${Else}
    StrCpy $Turkey "0"
  ${EndIf}
  ${NSD_GetState} $hStartWin $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $StartWin "1"
  ${Else}
    StrCpy $StartWin "0"
  ${EndIf}
  ${NSD_GetText} $hImport $ImportDir
  ${NSD_GetText} $hSrc $SrcAddr
  ${If} $ImportDir != ""
    !insertmacro TrimSlash $ImportDir
    ${IfNot} ${FileExists} "$ImportDir\config\*.yaml"
      MessageBox MB_ICONEXCLAMATION "$ImportDir has no config folder with settings in it. Choose the folder of the earlier copy (it has config and data inside), or leave the field empty."
      Abort
    ${EndIf}
  ${EndIf}
FunctionEnd

Function OpenApp
  ; through Explorer, so the window runs as the signed-in user and not with the Setup's administrator rights
  SetShellVarContext all
  Exec '"$WINDIR\explorer.exe" "$SMPROGRAMS\${APPNAME}.lnk"'
FunctionEnd

; ---- install ------------------------------------------------------------------------------------------------------
Section "Install"
  SetRegView 64
  SetShellVarContext all
  ReadEnvStr $9 PROGRAMDATA
  StrCpy $9 "$9\${APPNAME}"
  StrCpy $FinishText "${APPNAME} ${VERSION} is installed.$\r$\n$\r$\nThe background service runs on its own from now on: with the window closed, and after a restart. Open the window any time from the Start Menu or the Desktop.$\r$\n$\r$\nSettings and history: $9"

  DetailPrint "Stopping the background service, if it runs..."
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "${SRC}\scripts\install.ps1"
  nsExec::ExecToLog '${PS} -File "$PLUGINSDIR\install.ps1" -Phase Stop -InstallDir "$INSTDIR"'
  Pop $0

  ; the program files: replaced as a whole (the settings and the data are not in this folder)
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\plantcollector"
  RMDir /r "$INSTDIR\profiles"
  RMDir /r "$INSTDIR\config"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\scripts"
  RMDir /r "$INSTDIR\wheels"
  RMDir /r "$INSTDIR\.venv"
  SetOutPath "$INSTDIR"
  File /r "${STAGE}\*.*"
  WriteUninstaller "$INSTDIR\uninstall.exe"

  ; this Setup is kept: the updater puts it back if a later version does not start
  CreateDirectory "$9\update"
  ${If} "$EXEPATH" != "$9\update\installed-setup.exe"
    CopyFiles /SILENT "$EXEPATH" "$9\update\installed-setup.exe"
  ${EndIf}

  ; the choices (an update reads them back) and the entry in Settings > Apps
  WriteRegStr HKLM "${REGKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "${REGKEY}" "Version" "${VERSION}"
  WriteRegStr HKLM "${REGKEY}" "AutoUpdate" "$AutoUpd"
  WriteRegStr HKLM "${REGKEY}" "Firewall" "$Fw"
  WriteRegStr HKLM "${REGKEY}" "Desktop" "$Desk"
  ${If} $Turkey != ""
    WriteRegStr HKLM "${REGKEY}" "Turkey" "$Turkey"
  ${EndIf}
  ${If} $StartWin != ""
    WriteRegStr HKLM "${REGKEY}" "StartWindow" "$StartWin"
  ${EndIf}
  WriteRegStr HKLM "${REGKEY}" "SourceAddress" "$SrcAddr"
  WriteRegStr HKLM "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKLM "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${UNINSTKEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKLM "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\CCI Plant Monitoring.exe,0"
  WriteRegStr HKLM "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINSTKEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKLM "${UNINSTKEY}" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegStr HKLM "${UNINSTKEY}" "URLInfoAbout" "https://github.com/Tishkos/applicationCocaCola"
  WriteRegDWORD HKLM "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${UNINSTKEY}" "NoRepair" 1

  ; Python, packages, background service, updater, firewall, shortcuts, start
  DetailPrint "Setting up the background service, the updater, the firewall and the shortcuts..."
  StrCpy $1 '${PS} -File "$INSTDIR\scripts\install.ps1" -Phase Install -InstallDir "$INSTDIR" -AutoUpdate $AutoUpd -Firewall $Fw -Desktop $Desk -Version ${VERSION}'
  ${If} $ImportDir != ""
    !insertmacro TrimSlash $ImportDir
    StrCpy $1 '$1 -Import "$ImportDir"'
  ${EndIf}
  ${If} $SrcAddr != ""
    StrCpy $1 '$1 -SourceAddress "$SrcAddr"'
  ${EndIf}
  ${If} $Turkey != ""
    StrCpy $1 '$1 -Turkey $Turkey'
  ${EndIf}
  ${If} $StartWin != ""
    StrCpy $1 '$1 -StartWindow $StartWin'
  ${EndIf}
  nsExec::ExecToLog $1
  Pop $0
  ${If} $0 == "20"
    StrCpy $FinishText "${APPNAME} ${VERSION} is installed, but the background service did not answer within 2 minutes.$\r$\n$\r$\nIt keeps trying by itself; restart the PC if the window stays on 'Starting'. The details are in $9\install.log."
    SetErrorLevel 3
  ${ElseIf} $0 != "0"
    StrCpy $FinishText "The set-up did not finish (code $0).$\r$\n$\r$\nThe details are in $9\install.log. Fix what it says and run this Setup again; nothing of your settings or history is lost."
    MessageBox MB_ICONEXCLAMATION "The set-up did not finish (code $0). See $9\install.log." /SD IDOK
    SetErrorLevel 2
  ${EndIf}

  ${GetSize} "$INSTDIR" "/S=0K" $2 $3 $4
  IntFmt $2 "0x%08X" $2
  WriteRegDWORD HKLM "${UNINSTKEY}" "EstimatedSize" "$2"
SectionEnd

; ---- uninstall ----------------------------------------------------------------------------------------------------
Section "un.Program, background service, updater, firewall rules, shortcuts" SecProg
  SectionIn RO
  SetRegView 64
  SetShellVarContext all
  DetailPrint "Stopping and removing the background service and the updater..."
  ${If} ${FileExists} "$INSTDIR\scripts\uninstall.ps1"
    nsExec::ExecToLog '${PS} -File "$INSTDIR\scripts\uninstall.ps1" -InstallDir "$INSTDIR"'
    Pop $0
  ${Else}
    nsExec::ExecToLog '"$SYSDIR\schtasks.exe" /End /TN PlantDataCollector'
    Pop $0
    nsExec::ExecToLog '"$SYSDIR\schtasks.exe" /Delete /TN PlantDataCollector /F'
    Pop $0
    nsExec::ExecToLog '"$SYSDIR\schtasks.exe" /Delete /TN PlantCollectorUpdater /F'
    Pop $0
  ${EndIf}
  Delete "$SMPROGRAMS\${APPNAME}.lnk"
  Delete "$DESKTOP\${APPNAME}.lnk"
  ; only what the Setup put there (never the whole folder: it might hold something else)
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\plantcollector"
  RMDir /r "$INSTDIR\profiles"
  RMDir /r "$INSTDIR\config"
  RMDir /r "$INSTDIR\app"
  RMDir /r "$INSTDIR\scripts"
  RMDir /r "$INSTDIR\wheels"
  RMDir /r "$INSTDIR\.venv"
  Delete "$INSTDIR\CCI Plant Monitoring.exe"
  Delete "$INSTDIR\CCI Plant Monitoring Service.exe"
  Delete "$INSTDIR\requirements.txt"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\version.txt"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${UNINSTKEY}"
  DeleteRegKey HKLM "${REGKEY}"
SectionEnd

Section /o "un.Collected data and settings (C:\ProgramData\CCI Plant Monitoring)" SecData
  ReadEnvStr $9 PROGRAMDATA
  DetailPrint "Deleting the settings and the collected history..."
  RMDir /r "$9\${APPNAME}"
SectionEnd

Function un.onInit
  SetRegView 64
  SetShellVarContext all
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/DELETEDATA" $R1
  ${IfNot} ${Errors}
    !insertmacro SelectSection ${SecData}
  ${EndIf}
FunctionEnd
