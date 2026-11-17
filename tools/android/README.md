# Android compile canary

A temporary compile canary, not a build path. `CMakeLists.txt` here compiles a
hand-picked list of framework sources for arm64-v8a with the pinned NDK, outside
the mirror, so a change that breaks the framework under Android shows up before
the real lane exists. It produces no artifact that anyone consumes and nothing
installs or links its output.

It is replaced by the Android closure lane once the mirror's multi-platform
rule enables the android-arm64 target. When that lands, delete this directory
and `.github/workflows/android.yml`.

`check_android_objects.py` reads the ELF header of every object the build
produced and refuses anything that is not 64-bit AArch64, so a build that
silently used the host compiler cannot pass. Its tests include the cases that
must fail.
