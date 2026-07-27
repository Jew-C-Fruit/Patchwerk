/* Patchwerk.app's CFBundleExecutable.
 *
 * WHY THIS IS A COMPILED BINARY AND NOT A SHELL SCRIPT.
 *
 * This existed as a two-line `sh` script that exec'd the bundled Python.
 * It worked, and it silently broke the microphone permission — which is the
 * one thing the macOS bundle exists to fix. Measured on 2026-07-26 by
 * reading tccd's own attribution log:
 *
 *   AUTHREQ_ATTRIBUTION: attribution={responsible={identifier=python3.12,
 *     responsible_path=.../Patchwerk.app/Contents/Resources/python/bin/
 *     python3.12}, requesting={identifier=scsynth, ...}}
 *
 * TCC named the PYTHON BINARY as the responsible process, not
 * com.patchwerk.app. Two reasons, and the fix has to beat both:
 *
 *   1. `exec` replaces the process image, so the executable path became the
 *      interpreter under Contents/Resources — which is not a bundle main
 *      executable, so TCC resolves no bundle and reads no Info.plist. With
 *      no NSMicrophoneUsageDescription in play there is nothing to prompt
 *      WITH, so no dialog appears and the request just fails: scsynth
 *      enumerates devices and blocks forever, indistinguishable from the
 *      agent-session stall item 38 diagnosed.
 *   2. A `#!` script cannot fix this by not exec'ing, because the process
 *      image for a script IS its interpreter (/bin/sh). TCC would see
 *      /bin/sh, which is worse.
 *
 * So: a real Mach-O binary at Contents/MacOS/Patchwerk, which FORKS the
 * interpreter and WAITS. Because it never exec's, it remains the process
 * LaunchServices started, its path is the bundle's main executable, and TCC
 * resolves com.patchwerk.app — Info.plist, usage string, and a prompt that
 * says "Patchwerk". This is why py2app and PyInstaller ship a stub binary
 * rather than a script; we arrived at it the long way round.
 *
 * It also has to forward signals: quitting the app must reach the launcher,
 * which is what reaps the engine and its scsynth. A stub that ignored
 * SIGTERM would leave audio running with nothing on screen to stop it.
 */

#include <errno.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static volatile pid_t g_child = 0;

static void forward(int sig) {
    if (g_child > 0) kill(g_child, sig);
}

int main(int argc, char **argv) {
    char exe[PATH_MAX];
    uint32_t len = sizeof(exe);
    if (_NSGetExecutablePath(exe, &len) != 0) {
        fprintf(stderr, "Patchwerk: cannot locate my own executable\n");
        return 1;
    }
    char resolved[PATH_MAX];
    if (realpath(exe, resolved) == NULL)
        snprintf(resolved, sizeof(resolved), "%s", exe);

    /* <bundle>/Contents/MacOS/Patchwerk -> <bundle>/Contents.
       dirname() may write to its argument, so give it copies. */
    char buf[PATH_MAX];
    snprintf(buf, sizeof(buf), "%s", resolved);
    char macos[PATH_MAX];
    snprintf(macos, sizeof(macos), "%s", dirname(buf));
    snprintf(buf, sizeof(buf), "%s", macos);
    char contents[PATH_MAX];
    snprintf(contents, sizeof(contents), "%s", dirname(buf));

    char python[PATH_MAX], script[PATH_MAX];
    snprintf(python, sizeof(python), "%s/Resources/python/bin/python3", contents);
    snprintf(script, sizeof(script), "%s/Resources/launcher.py", contents);

    if (access(python, X_OK) != 0) {
        fprintf(stderr, "Patchwerk: bundled Python missing at %s\n", python);
        return 1;
    }

    g_child = fork();
    if (g_child < 0) {
        perror("Patchwerk: fork");
        return 1;
    }
    if (g_child == 0) {
        char **av = (char **)calloc((size_t)argc + 3, sizeof(char *));
        if (av == NULL) _exit(127);
        av[0] = python;
        av[1] = script;
        for (int i = 1; i < argc; i++) av[i + 1] = argv[i];
        av[argc + 1] = NULL;
        execv(python, av);
        perror("Patchwerk: execv");
        _exit(127);
    }

    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);

    int status = 0;
    while (waitpid(g_child, &status, 0) < 0) {
        if (errno != EINTR) break;
    }
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return 1;
}
