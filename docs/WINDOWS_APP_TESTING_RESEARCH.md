# Testing Windows desktop applications — research and options for testbot

*Written 2026-10-02. Sources are linked at the end. Statements marked **[general knowledge]** come from my own background knowledge, not from a source I read in this research, and should be checked before they are relied on.*

## 1. Short answer

There is **no single way** to test "a Windows app". The right tool depends on **how the app draws its screen**:

| If the app is built with… | Best way to drive it | Why |
|---|---|---|
| **Electron** or a **WebView2** window (a web page inside a desktop shell) | **Playwright** over the Chrome DevTools Protocol (CDP) | It is the same engine testbot already uses. Selectors, waits, assertions, screenshots all work unchanged. |
| **WPF, WinForms, Win32, UWP/WinUI** (standard Windows controls) | **Microsoft UI Automation (UIA)** — through **pywinauto** (Python), **FlaUI** (.NET) or an **Appium** Windows driver | UIA is the accessibility tree Windows gives every standard control: names, ids, types, values, and actions (click, set text, select). No pixel guessing. |
| **Qt, Java Swing, SAP GUI, Delphi, games, remote sessions (Citrix/RDP)** or any **custom-drawn** UI | A tool with its own object model for that toolkit (Squish for Qt/Java, TestComplete, Ranorex, UiPath), or **image / vision-based** automation | UIA often sees only one big window, or generic panes. |

For testbot specifically, the best fit is a **desktop "target type" built on UI Automation (pywinauto)**, plus a **Playwright-over-CDP route for Electron/WebView2**, and a **vision-based fallback** for custom-drawn parts. Section 5 gives the options and a recommendation.

**I do not know which Windows application you want to test.** That decides almost everything below (see the questions in section 7).

## 2. How desktop testing works (the building blocks)

**UI Automation (UIA)** is Microsoft's accessibility framework. It exposes the UI as a *tree of automation elements*; each element has properties (name, automation id, control type, class, enabled, bounding box) and *control patterns* (Invoke for buttons, Value for text boxes, Selection, ExpandCollapse, Grid, …) that let a client act on it without synthesising mouse clicks. Screen readers and test tools use the same API. ([Microsoft: UI Automation overview](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview))

**Inspecting the tree.** Before writing any test you look at the app's UIA tree. `Inspect.exe` (Windows SDK) does this but is a legacy tool; Microsoft recommends **Accessibility Insights for Windows**, which shows each element's position in the tree with its properties and patterns. ([Inspect](https://learn.microsoft.com/en-us/windows/win32/winauto/inspect-objects), [Accessibility Insights for Windows](https://devblogs.microsoft.com/engineering-at-microsoft/accessibility-insights-for-windows/))

**The rule that decides stability:** tests are stable when each control has a stable **AutomationId** (set by the developers). Locating by visible text breaks with language or wording changes; locating by position or image breaks with layout, theme or DPI changes. If the app's developers can add AutomationIds, testing gets far cheaper. **[general knowledge, consistent with the tool vendors' guidance in the sources]**

## 3. The tools, and their status today

| Tool | What it is | Status (per sources) | Fit |
|---|---|---|---|
| **pywinauto** | Python library; two backends: `win32` and `uia`. Attribute-based control access instead of coordinates. BSD license. | Active, community-maintained. I could not confirm the latest release or date from the docs page I fetched. | **Best fit for testbot** (Python, same process, no extra runtime). |
| **FlaUI** | .NET wrapper over UIA, with UIA2 and UIA3 packages. MIT license. Win32, WinForms, WPF, Store apps. v5.0.0 released Feb 2025. | Active. The FlaUI docs say UIA3 suits WPF/Store apps better but "can have some bugs with WinForms", where UIA2 is more stable. | Best choice if the test code were .NET. For testbot it would mean a separate .NET helper process. |
| **WinAppDriver** (Microsoft) | WebDriver-protocol server for Windows apps. | **Not maintained**: no stable release since v1.2.1 (Nov 2020), closed source. The Appium Windows driver README says it "has not been maintained by Microsoft for years". **Do not start new work on it.** | Avoid. |
| **Appium Windows driver** | Appium's proxy to WinAppDriver; supports Windows 10 as host. | Inherits WinAppDriver's problems. | Avoid for new work. |
| **NovaWindows driver** (Appium 2) | Community driver by Automate The Planet, recommended in the Appium Windows driver README as a drop-in replacement; WPF, WinForms, UWP, Win32; release 1.4.1 (June 2026). | Active. | Option if you want the standard WebDriver/Appium ecosystem. Adds Node.js + Appium as dependencies. I did not verify it hands-on. |
| **Playwright** (Electron / WebView2) | Electron: `_electron.launch` (marked experimental). WebView2: start the app with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`, then `chromium.connect_over_cdp("http://localhost:9222")` and use the first context/page. For parallel runs give each instance its own `WEBVIEW2_USER_DATA_FOLDER`. | Official Playwright feature. | **Easiest win** if the app is Electron or WebView2. |
| **TestComplete / Ranorex / Squish** | Commercial; object recognition for many toolkits plus recorders and reporting. Squish is strongest for Qt and Java. | Active, paid. I did not check prices or licences. | Consider only if the app uses toolkits UIA cannot see (Qt, Java, SAP). |
| **SikuliX / image matching** | OpenCV template matching on screenshots. | Works anywhere there is pixels, but needs manual image updates whenever the UI changes; object-based tools are generally more stable and faster. | Last-resort fallback. |
| **AI/vision agents (Microsoft UFO / UFO²)** | Research agent for Windows. Uses the UIA tree as its main source, and UFO² adds a hybrid of UIA plus visual grounding for custom UI components. Open source. | Active research project (UFO³ extends to multi-device). | The model for an "agentic" desktop mode (section 5). Research-grade, not a drop-in product. |

## 4. Known limits and traps

- **Custom-drawn UI is a blind spot for UIA.** Controls that paint themselves (some Delphi/Qt/Java/SAP/game UIs, remote-desktop pixels) may appear as one generic element. UFO² exists partly for this reason: it combines UIA with visual grounding. Citrix automation needs remote-side components (UiPath's documentation describes that for its own product). **[the general point is well established; I found no source on Delphi specifically]**
- **A desktop test needs an interactive desktop.** A locked workstation or a minimised/disconnected RDP session generally breaks UI input; CI agents must run in a logged-in session (or a VM with auto-logon). **[general knowledge]**
- **Elevation:** a test process that is not elevated generally cannot drive an app running "as administrator". **[general knowledge]**
- **Bitness / DPI / language:** 32- vs 64-bit mismatch can matter for some automation libraries; DPI scaling and language change layout and text. I could not confirm pywinauto's exact bitness constraints from its docs page. **[to verify in a pilot]**
- **Timing:** desktop apps need explicit waits for windows and controls to appear (testbot's `wait_until` idea carries over).
- **Evidence:** screenshots of a *window* or the screen, not a page. testbot's current IACF logo watermark is drawn into the web page, so it would need a different method for desktop screenshots (for example compositing with Pillow, which was removed from the build to save size).
- **Security / data:** tests drive real apps and may type real credentials. The log masking added in this project covers values typed into password-like fields; the same rule should apply to desktop steps.

## 5. Options for testbot

All three keep testbot's test files (JSON/Excel), variables, SQL steps, reports, manager UI and logs. They differ in how the desktop is driven.

**Option A — "desktop" target type on pywinauto (UIA) inside testbot** *(recommended core)*
- New actions: `launch_app`, `attach_app`, `close_app`; existing actions (`click`, `type`, `select`, `check`, `assert`, `wait_until`) accept a *desktop target*: window title + automation id / name / control type / class, plus `nth` and a parent chain.
- Python only, same process; no new runtime for testers. Needs the `comtypes`/`pywin32` stack in the build (size grows).
- Recorder: a UIA recorder is possible (hook the control under the cursor) but is a larger piece of work than the browser recorder.
- Risk: custom-drawn UI (section 4).

**Option B — Appium + NovaWindows driver**
- testbot would speak the WebDriver protocol to an Appium server. Standard, with a large ecosystem and a path to other platforms.
- Cost: Node.js and Appium must be installed and managed (a poor fit for the "one folder, no installs" packaging goal). The driver is community-maintained.

**Option C — Playwright over CDP, for Electron / WebView2 apps only**
- Smallest change: a step/suite setting that launches the executable with the debug-port variable (WebView2) or `_electron.launch`, then runs the *existing* web steps against the app's page. The AI agent mode would also work as is.
- Limit: only the web content inside the app; native menus, file dialogs and OS windows are not reachable.

**Add-on D — vision fallback and agentic mode (later)**: let the existing agent observe the UIA tree (like the browser observer does) and use screenshots plus the model to click what UIA cannot see. UFO² shows the hybrid pattern. This is exploratory and costs model tokens.

**Recommendation.** Decide by app type: **C first** if the application is Electron/WebView2 (days of work, reuses everything); otherwise **A** (weeks), with **D** added only where UIA is blind. **Avoid B** unless you already run Appium. **Do not adopt WinAppDriver.**

## 6. Suggested pilot (1–2 days, before any design commitment)

1. Pick one real Windows app and one 5–10 step scenario.
2. Open it in **Accessibility Insights for Windows** and check: do the controls have names / AutomationIds? Is anything custom-drawn? Is it Electron/WebView2 (check for the Chromium process)?
3. If standard controls: drive the scenario with a short **pywinauto (UIA backend)** script; if Electron/WebView2: with Playwright over CDP.
4. Record: stability across two runs, time per step, what could not be located, and what the app's developers would need to add (AutomationIds).
5. Only then decide between A, C and D.

## 7. Questions that change the plan

1. Which application(s)? Built with WPF / WinForms / Win32 / Electron / WebView2 / Qt / Java / SAP / Delphi — or unknown?
2. Is it **already tested through its web part** (as with Apriso), and the Windows app is a different one — or is the goal to test a desktop *client* of the same system?
3. Can the developers add stable **AutomationIds**?
4. Where will tests run (a developer PC, a VM, a shared build agent) — is there an interactive logged-in desktop?
5. Do testers need a **recorder** for it, or is hand-written JSON/Excel enough at first?

## 8. What I could not verify

- pywinauto's current release number and exact maintenance status (the docs page I fetched did not state them).
- NovaWindows driver quality and feature set: only vendor/ecosystem descriptions were read, no hands-on test.
- Commercial tool prices and licensing.
- Playwright's Electron support is described by Playwright as **experimental**; I did not check how it behaves with the current Electron versions.
- Any claim marked **[general knowledge]** above.

## Sources

- Tool landscape and status: [10 Best Desktop Testing Tools (AccelQ)](https://www.accelq.com/blog/desktop-application-testing-tools/), [UI Automated Testing for Desktop Apps — FlaUI in Practice](https://comcomponent.com/en/blog/windows-desktop-ui-automation-testing/), [TestDriver: pywinauto alternatives](https://testdriver.ai/articles/top-4-alternatives-to-pywinauto-for-desktop-ui/)
- WinAppDriver / Appium: [appium-windows-driver README](https://github.com/appium/appium-windows-driver), [NovaWindows Driver for Appium 2 (Automate The Planet)](https://www.automatetheplanet.com/reviving-windows-app-automation-novawindows-driver-for-appium-2/), [Appium drivers](https://appium.io/docs/en/3.1/ecosystem/drivers/), [WinAppDriver migration guide](https://www.automatetheplanet.com/winappdriver-to-appium-migration-guide/)
- FlaUI: [github.com/FlaUI/FlaUI](https://github.com/FlaUI/FlaUI)
- pywinauto: [pywinauto documentation](https://pywinauto.readthedocs.io/en/latest/)
- Playwright for Electron / WebView2: [Playwright WebView2 (Python)](https://playwright.dev/python/docs/webview2), [Electron: automated testing](https://www.electronjs.org/docs/latest/tutorial/automated-testing)
- UI Automation and inspection tools: [UI Automation overview](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview), [Inspect](https://learn.microsoft.com/en-us/windows/win32/winauto/inspect-objects), [Accessibility Insights for Windows](https://devblogs.microsoft.com/engineering-at-microsoft/accessibility-insights-for-windows/)
- AI agents for Windows: [UFO: A UI-Focused Agent for Windows OS Interaction (Microsoft Research)](https://www.microsoft.com/en-us/research/publication/ufo-a-ui-focused-agent-for-windows-os-interaction/), [UFO² (arXiv)](https://arxiv.org/html/2504.14603v1)
- Commercial and image-based tools: [Squish for Windows](https://www.qt.io/quality-assurance/squish/platform-automated-windows-gui-testing), [Comparison of GUI testing tools](https://en.wikipedia.org/wiki/Comparison_of_GUI_testing_tools), [AskUI vs SikuliX](https://www.askui.com/blog-posts/askui-vs-sikulix-visual-automation-comparison-2025)
- Citrix / SAP notes (UiPath docs): [Automating Citrix technologies](https://docs.uipath.com/studio/standalone/2023.4/user-guide/automating-citrix-technologies)
