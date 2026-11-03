/* CCI Plant Monitoring.exe and CCI Plant Monitoring Service.exe: the program's own Python (runtime\python313.dll)
 * started in this process, so Windows shows these names and this icon (Task Manager, taskbar, firewall prompts)
 * instead of python.exe. Nothing on the PC is needed besides Windows itself.
 *
 *   window  (built without SERVICE): runtime + app\desktop.py --installed --data <ProgramData>\CCI Plant Monitoring\data
 *   service (built with -DSERVICE):  runtime + -m plantcollector.cli <the arguments of the Windows task>
 *
 * While the Setup replaces the files it leaves <ProgramData>\CCI Plant Monitoring\update\installing.flag; for 30 minutes
 * after that file was written, neither exe starts (no file of the program is held open while it is replaced).
 *
 * Both are GUI programs (no console window), like pythonw.exe. PYTHONHOME and PYTHONPATH are set to this folder's
 * runtime and program, so another Python on the PC (or its environment variables) can never be picked up.
 */
#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#include <windows.h>
#include <shellapi.h>
#include <stdlib.h>
#include <wchar.h>

typedef int (*py_main_t)(int, wchar_t **);

static void fail(const wchar_t *what, const wchar_t *detail)
{
#ifdef SERVICE
    (void)what; (void)detail;          /* no one to show a message box to: the task restarts it */
#else
    wchar_t msg[2048];
    swprintf(msg, 2048, L"%ls\n\n%ls\n\nRun the CCI Plant Monitoring Setup again to repair the installation.", what, detail);
    MessageBoxW(NULL, msg, L"CCI Plant Monitoring", MB_ICONERROR | MB_OK);
#endif
}

/* true while the Setup is replacing the program (installing.flag written less than 30 minutes ago) */
static int updating(const wchar_t *programdata)
{
    wchar_t flag[MAX_PATH];
    WIN32_FILE_ATTRIBUTE_DATA fa;
    swprintf(flag, MAX_PATH, L"%ls\\CCI Plant Monitoring\\update\\installing.flag", programdata);
    if (!GetFileAttributesExW(flag, GetFileExInfoStandard, &fa)) return 0;
    FILETIME now;
    GetSystemTimeAsFileTime(&now);
    ULARGE_INTEGER a, b;
    a.LowPart = fa.ftLastWriteTime.dwLowDateTime; a.HighPart = fa.ftLastWriteTime.dwHighDateTime;
    b.LowPart = now.dwLowDateTime; b.HighPart = now.dwHighDateTime;
    return b.QuadPart < a.QuadPart + 30ULL * 60ULL * 10000000ULL;     /* 100 ns units */
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, LPWSTR cmdline, int show)
{
    (void)inst; (void)prev; (void)cmdline; (void)show;
    static wchar_t dir[MAX_PATH], rt[MAX_PATH], dll[MAX_PATH];
    DWORD n = GetModuleFileNameW(NULL, dir, MAX_PATH);
    if (!n || n >= MAX_PATH) { fail(L"Could not find the program folder.", L""); return 1; }
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;
    swprintf(rt, MAX_PATH, L"%ls\\runtime", dir);
    static wchar_t pd[MAX_PATH];
    DWORD m = GetEnvironmentVariableW(L"ProgramData", pd, MAX_PATH);
    if (!m || m >= MAX_PATH) wcscpy(pd, L"C:\\ProgramData");
    if (updating(pd)) {
#ifndef SERVICE
        MessageBoxW(NULL, L"CCI Plant Monitoring is being updated right now.\n\nOpen it again in a minute.",
                    L"CCI Plant Monitoring", MB_ICONINFORMATION | MB_OK);
#endif
        return 6;
    }
    swprintf(dll, MAX_PATH, L"%ls\\python313.dll", rt);

    /* only this program's Python, whatever else is set on the PC */
    SetEnvironmentVariableW(L"PYTHONHOME", rt);
    SetEnvironmentVariableW(L"PYTHONPATH", dir);
    SetEnvironmentVariableW(L"PYTHONNOUSERSITE", L"1");
    SetEnvironmentVariableW(L"PYTHONSTARTUP", NULL);
    SetEnvironmentVariableW(L"PYTHONSAFEPATH", NULL);
    SetEnvironmentVariableW(L"PYTHONEXECUTABLE", NULL);
    SetDllDirectoryW(rt);              /* the runtime's DLLs (vcruntime140, sqlite3, libcrypto, ...) */

    HMODULE py = LoadLibraryExW(dll, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!py) { fail(L"The program's Python runtime is missing or damaged:", dll); return 2; }
    py_main_t py_main = (py_main_t)(void *)GetProcAddress(py, "Py_Main");
    if (!py_main) { fail(L"The program's Python runtime is damaged (no Py_Main):", dll); return 3; }

    int argc = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) { fail(L"Could not read the command line.", L""); return 4; }

#ifdef SERVICE
    const int fixed = 3;
    wchar_t **args = (wchar_t **)calloc((size_t)(argc + fixed + 1), sizeof(wchar_t *));
    if (!args) return 5;
    args[0] = argv[0];
    args[1] = L"-m";
    args[2] = L"plantcollector.cli";
#else
    static wchar_t script[MAX_PATH], data[MAX_PATH];
    swprintf(script, MAX_PATH, L"%ls\\app\\desktop.py", dir);
    swprintf(data, MAX_PATH, L"%ls\\CCI Plant Monitoring\\data", pd);
    const int fixed = 5;
    wchar_t **args = (wchar_t **)calloc((size_t)(argc + fixed + 1), sizeof(wchar_t *));
    if (!args) return 5;
    args[0] = argv[0];
    args[1] = script;
    args[2] = L"--installed";
    args[3] = L"--data";
    args[4] = data;
#endif
    for (int i = 1; i < argc; i++) args[fixed + i - 1] = argv[i];
    int total = fixed + argc - 1;
    args[total] = NULL;
    return py_main(total, args);
}
