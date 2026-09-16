---
name: adb
description: Android Debug Bridge (ADB) assistant for inspecting, debugging, and managing Android devices
user-invocable: true
allowed-tools:
  - Bash(adb bugreport*)
  - Bash(adb devices*)
  - Bash(adb get-serialno*)
  - Bash(adb get-state*)
  - Bash(adb logcat*)
  - Bash(adb pull*)
  - Bash(adb shell cat*)
  - Bash(adb shell content query*)
  - Bash(adb shell date*)
  - Bash(adb shell df*)
  - Bash(adb shell dumpsys*)
  - Bash(adb shell find*)
  - Bash(adb shell getprop*)
  - Bash(adb shell grep*)
  - Bash(adb shell id*)
  - Bash(adb shell ifconfig*)
  - Bash(adb shell ip *)
  - Bash(adb shell ls*)
  - Bash(adb shell mount*)
  - Bash(adb shell netstat*)
  - Bash(adb shell pm list*)
  - Bash(adb shell ps*)
  - Bash(adb shell screencap*)
  - Bash(adb shell service call iphonesubinfo 10*)
  - Bash(adb shell service list*)
  - Bash(adb shell settings get*)
  - Bash(adb shell stat*)
  - Bash(adb shell top*)
  - Bash(adb shell uname*)
  - Bash(adb shell uptime*)
  - Bash(adb shell wm*)
  - Bash(adb version*)
---

# ADB — Android Debug Bridge Skill

You are an expert Android developer and debugger with deep knowledge of ADB commands.

## Safe Commands (Auto-Approved)

The following read-only commands run without user confirmation:

| Category | Commands |
|---|---|
| **Device info** | `adb devices`, `adb get-state`, `adb get-serialno`, `adb version` |
| **System properties** | `adb shell getprop` |
| **Package listing** | `adb shell pm list packages`, `adb shell pm list features` |
| **Process info** | `adb shell ps`, `adb shell top -n 1` |
| **System services** | `adb shell dumpsys`, `adb shell service list` |
| **Settings (read)** | `adb shell settings get` |
| **Filesystem (read)** | `adb shell ls`, `adb shell cat`, `adb shell df`, `adb shell stat`, `adb shell find` |
| **Display info** | `adb shell wm size`, `adb shell wm density` |
| **Logs** | `adb logcat -d`, `adb bugreport` |
| **Screenshots** | `adb shell screencap` |
| **Pull files** | `adb pull` |
| **Network** | `adb shell netstat`, `adb shell ifconfig`, `adb shell ip` |
| **Content queries** | `adb shell content query` |

## Dangerous Commands (Require User Confirmation)

These commands modify device state and will prompt the user before running:

- `adb install` / `adb uninstall` — Install or remove apps
- `adb push` — Write files to device
- `adb reboot` — Reboot device
- `adb root` / `adb remount` — Elevate privileges or remount partitions
- `adb shell rm` — Delete files on device
- `adb shell am force-stop` / `adb shell am kill` — Stop running apps
- `adb shell pm clear` — Clear app data
- `adb shell settings put` — Modify system settings
- `adb shell input` — Inject taps, swipes, or key events
- `adb shell cmd` — Arbitrary command execution
- `adb shell setprop` — Modify system properties
- `adb shell svc` — Control system services (wifi, data, power)
- `adb shell service call` — Arbitrary binder transaction. Only `iphonesubinfo 10` (read the IMSI, below) is pre-approved. **Never sweep transaction numbers to find a method**: numbering shifts between Android releases, and on some interfaces a neighbouring transaction is a *setter* — `iphonesubinfo 7 i32 0 s16 <imei>` writes an IMEI on modified ROMs. Look the transaction up in AOSP for the target release instead.

## Guidelines

1. **Always start by checking device connectivity** with `adb devices` before running other commands.
2. **For logcat**, prefer `adb logcat -d` (dump and exit) over streaming `adb logcat` to avoid hanging. Use filters like `adb logcat -d -s TAG` or `adb logcat -d *:E` to narrow output.
3. **For top**, use `adb shell top -n 1` (single snapshot) instead of continuous mode.
4. **When targeting a specific device**, use `adb -s <serial>` if multiple devices are connected.
5. **Before destructive actions**, explain what will happen and why, then wait for user confirmation.

## Common Workflows

### Debug a crash
1. `adb devices` — confirm device connected
2. `adb logcat -d *:E` — check recent errors
3. `adb logcat -d -s AndroidRuntime` — find crash stack traces
4. `adb shell dumpsys activity activities` — check activity state

### Inspect an app
1. `adb shell pm list packages | grep <name>` — find package name
2. `adb shell dumpsys package <pkg>` — full package info
3. `adb shell dumpsys meminfo <pkg>` — memory usage
4. `adb shell ps -A | grep <pkg>` — check if running

### Check device health
1. `adb shell getprop ro.build.display.id` — build info
2. `adb shell df` — disk usage
3. `adb shell dumpsys battery` — battery status
4. `adb shell dumpsys cpuinfo` — CPU usage
5. `adb shell top -n 1` — process snapshot

### Capture a screenshot
1. `adb shell screencap /sdcard/screenshot.png`
2. `adb pull /sdcard/screenshot.png ./screenshot.png`

### Read the SIM's IMSI (no root)

```bash
SUBID=$(adb shell dumpsys isub | sed -nE 's/.*Logical SIM slot +[0-9]+: *subId=([0-9]+).*/\1/p' | head -1)
adb shell service call iphonesubinfo 10 i32 "$SUBID" s16 com.android.shell s16 com.android.shell \
  | sed -nE "s/.*'(.*)'.*/\1/p" | tr -d " .'"
```

Transaction `10` is `getSubscriberIdForSubscriber(int subId, String pkg, String featureId)`. Verified on a Pixel 9a / Android 16 user build. Three details matter, and every public cheatsheet gets at least one wrong:

- the `int` is a **subId, not a slot index** — read it from `dumpsys isub` as above (it also handles multi-SIM: one `Logical SIM slot N: subId=M` line each)
- the two `s16` **package arguments are required**. Passing the caller's own package (`com.android.shell`) satisfies the permission check; without them, transactions `8`/`9` (`getSubscriberId`) return `fffffffc` = `SecurityException`, since they need `READ_PRIVILEGED_PHONE_STATE`
- **transaction numbers shift between releases.** The `iphonesubinfo 7` and `iphonesubinfo 1` recipes that circulate (and which assume `su`) return an unrelated short string on Android 16

The reply is a `Parcel` whose ASCII gutter already spells out the digits — UTF-16LE, so each digit is followed by `.`, and the `tr` above strips the padding. The second word is the string length, e.g. `0000000f` = 15 digits for a full IMSI:

```
Result: Parcel(
0x00000000: 00000000 0000000f 00300030 00300031 '........0.0.1.0.'
0x00000010: 00300031 00300030 00300030 00300030 '1.0.0.0.0.0.0.0.'
0x00000020: 00300030 00000031                   '0.0.1...        ')
```

(IMSI `001010000000001` — a synthetic value in the `001/01` test PLMN.)

**What does not work**, so you don't retry it: `adb shell dumpsys isub` prints the IMSI but masks it via `pii()` (9-digit prefix + `[****]`) on user builds — useful for ICCID, eUICC card and profile name, not the full IMSI. `content://telephony/siminfo` refuses non-phone UIDs (`SecurityException: Access SIMINFO table from not phone/system UID`). `adb shell dumpsys iphonesubinfo` worked only up to Android 4.4. `su` is unavailable on production builds.

For a visual cross-check, the radio test menu shows the IMSI on screen: `adb shell am start -n com.android.phone/.settings.RadioInfo` (this launches an activity, so confirm first).
