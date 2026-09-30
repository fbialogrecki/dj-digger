import QtQuick
import QtQuick.Controls
import QtQuick.Controls.impl
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    width: 1200; height: 800
    minimumWidth: 760; minimumHeight: 520
    visible: true
    title: "dj-digger" + (desktop.view.title ? " — " + desktop.view.title : "")
    property string languageCode: systemLanguage
    property string themeChoice: "system"
    property bool dark: themeChoice === "dark" || (themeChoice === "system" && Application.styleHints.colorScheme === Qt.Dark)
    // Club palette: near-black surfaces, electric blue and bordeaux accents, metallic silver text.
    property color bg: dark ? "#0b0b0e" : "#eceef1"
    property color panel: dark ? "#141418" : "#fafafb"
    property color fg: dark ? "#ececef" : "#111217"
    property color muted: dark ? "#a3a6ad" : "#50535c"
    property color alternate: dark ? "#1e1e24" : "#dfe2e7"
    property color hoverSurface: dark ? "#26262e" : "#d6dae1"
    property color pressedSurface: dark ? "#35353f" : "#c1c6cf"
    property color disabledFg: dark ? "#8e9098" : "#585b64"
    // Selection is a soft, low-chroma blue: a whole list selected must not glare.
    property color selection: dark ? "#34466f" : "#c9d5f2"
    property color selectionText: dark ? "#ffffff" : "#111217"
    property color accent: dark ? "#2f63ea" : "#1f4fd1"
    property color accent2: dark ? "#c02a4e" : "#8c1a36"
    property color silver: dark ? "#c9ccd3" : "#7d818b"
    property color success: dark ? "#6fd08c" : "#1c7a3c"
    property color warning: dark ? "#e3b85c" : "#8a5a00"
    property color danger: dark ? "#ff7088" : "#a3122f"
    // Tabular digits keep times, BPM and counts from jittering as they change.
    readonly property var tabular: ({ "tnum": 1 })
    property string keyNotation: "camelot"
    onKeyNotationChanged: desktop.model.setKeyNotation(keyNotation)
    property bool animations: true
    // Every transition checks this, so one switch turns motion off everywhere.
    readonly property bool motion: animations && !shuttingDown
    // Rows whose status just changed flash once; delegates created or reused in
    // this short window (after a sort or filter reset) still catch the flash.
    property var flashKeys: ({})
    Timer { id: flashTimer; interval: 700; onTriggered: root.flashKeys = ({}) }
    Connections {
        target: desktop.model
        function onStatusFlashed(keys) { if (root.motion) { root.flashKeys = keys; flashTimer.restart() } }
    }
    // Normal colours remain visible at rest; only detected bass attacks add saturation.
    readonly property bool live: desktop.playing
    // PCM has already entered the output queue. The offset compensates for the
    // speaker buffer, independently of detection (View → Pulse timing).
    property int pulseOffset: 70
    // The pulse envelope: a hit rises to its peak over 25 ms, holds 40 ms and
    // releases exponentially over a third of a beat (90-180 ms), so the glow
    // breathes with the kick instead of strobing. Later hits hold the peak and
    // never add up; hits under 100 ms apart merge into one swell.
    property real flash: 0
    property real peak: 0
    property real peakAt: 0
    property real peakFrom: 0
    readonly property real glow: flash
    // Pulses are copied once per change; the frame loop reads these, not the Python property.
    property string beatKey: ""
    property var beatPulses: []
    property real beatPeriod: .5
    Connections {
        target: desktop
        function onBeatsChanged() {
            root.beatKey = desktop.beats.key || ""
            root.beatPulses = desktop.beats.pulses || []
            root.beatPeriod = desktop.beats.period || .5
        }
    }
    readonly property real release: Math.max(90, Math.min(180, 300 * beatPeriod))
    readonly property bool pulsing: live && motion
    property real audioClock: -1
    property real clockAt: 0
    property real lastPosition: 0
    property string clockKey: ""
    property real lastFired: -1
    property real lastShown: -1
    // Heard audio time, interpolated every frame while pulsing; -1 falls back to the snapshot position.
    property real playhead: -1
    onPulsingChanged: if (!pulsing) { peak = 0; flash = 0; audioClock = -1; playhead = -1 }
    Connections {
        target: desktop
        function onAudioChanged() { if (root.pulsing) root.syncClock(desktop.audio.position, Date.now()) }
    }
    FrameAnimation {
        running: root.pulsing && root.visibility !== Window.Minimized
        onTriggered: root.pulseTick(Date.now())
    }
    function syncClock(position, now) {
        let predicted = audioClock + (now - clockAt) / 1000
        let key = desktop.audioKey
        if (audioClock < 0 || key !== clockKey || Math.abs(position - predicted) > .15) {
            audioClock = position
            // Include the first queued attack, even when it arrived just before
            // the first UI tick. pulseTick still rejects anything over 100 ms late.
            if (key !== clockKey || Math.abs(position - lastPosition) > .15)
                lastFired = position - pulseOffset / 1000 - .101
            peak = 0; flash = 0; lastShown = -1
            playhead = Math.max(0, position - pulseOffset / 1000)
        } else {
            audioClock = predicted + .25 * (position - predicted)
        }
        clockKey = key
        lastPosition = position
        clockAt = now
    }
    function pulseTick(now) {
        if (!pulsing || audioClock < 0) return
        let heard = Math.min(lastPosition, audioClock + (now - clockAt) / 1000 - pulseOffset / 1000)
        playhead = Math.max(0, heard)
        if (beatKey !== clockKey) return
        let pulses = beatPulses, due = -1
        for (let i = 0; i < pulses.length; i++)
            if (pulses[i][0] <= heard && pulses[i][0] > lastFired + .001) due = i
        if (due >= 0) {
            lastFired = pulses[due][0]
            if (heard - lastFired <= .1) {
                let amplitude = pulses[due][1]
                // A hit within half a beat of the last one shown is the roll, not the kick.
                if (lastShown >= 0 && lastFired - lastShown < .5 * beatPeriod) amplitude *= .6
                lastShown = lastFired
                if (peak > 0 && now - peakAt < 100) peak = Math.max(peak, amplitude)
                else { peakFrom = flash; peak = Math.max(amplitude, flash); peakAt = now }
            }
        }
        if (peak <= 0) return
        let age = now - peakAt
        flash = age < 25 ? peakFrom + (peak - peakFrom) * age / 25
              : age < 65 ? peak : peak * Math.exp(-(age - 65) / release)
        if (age >= 65 && flash < .005) { flash = 0; peak = 0 }
    }
    // One shared sweep for every download fill, running only while something is busy.
    property real shimmerPhase: 0
    NumberAnimation on shimmerPhase {
        from: 0; to: 1; duration: 1400; loops: Animation.Infinite
        running: root.motion && desktop.busy && root.visibility !== Window.Minimized
    }
    property real sidebarWidth: 220
    property bool sidebarVisible: true
    property bool shuttingDown: false
    property bool isMuted: false
    property real volume: desktop.volume
    property var pendingQuestions: []
    property var messages: []
    property string errorMessage: ""
    readonly property bool pauseTarget: desktop.playing && (!hasSelection || desktop.model.firstSelectedKey === desktop.audioKey)
    readonly property var defaultColumnWidths: [85, 150, 280, 85, 64, 64, 50, 95, 50, 140]
    property var columnWidths: defaultColumnWidths.slice()
    property var hiddenColumns: []
    // Logical column per visual position; TableView keeps delegates and widths logical.
    property var columnOrder: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    readonly property int titleColumn: 2
    function applyColumnOrder(order) {
        for (let visual = 0; visual < order.length; visual++) table.moveColumn(order[visual], visual)
    }
    function resetColumnOrder() {
        table.clearColumnReordering()
        columnOrder = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    }
    function localizeButtons(target) {
        let cancel = target.standardButton(Dialog.Cancel); if (cancel) cancel.text = qsTr("Cancel")
        let close = target.standardButton(Dialog.Close); if (close) close.text = qsTr("Close")
    }
    function columnAvailable(column) { return (column !== 4 && column !== 5) || !!desktop.view.local }
    function columnVisible(column) { return columnAvailable(column) && hiddenColumns.indexOf(column) < 0 }
    // Width of the widest visible value (or the header) in a column, in cell pixels.
    function contentWidth(column) {
        let sample = desktop.model.longestText(column)
        let needed = 0
        if (column === 9) {
            needed = 12
            for (let part of sample.split(", ").filter(s => s)) { badgeMetrics.text = part; needed += badgeMetrics.width + 14 }
        } else {
            fitMetrics.text = sample
            needed = fitMetrics.width + 14 + (column === titleColumn ? 26 : column === 4 || column === 5 ? 14 : 0)
        }
        fitMetrics.text = desktop.model.headerName(column) + " ▲"
        return Math.ceil(Math.max(40, Math.min(600, Math.max(needed, fitMetrics.width + 10))))
    }
    function fitColumn(column) {
        if (!columnVisible(column)) return
        let width = contentWidth(column)
        columnWidths[column] = width
        table.setColumnWidth(column, width)
        table.forceLayout()
    }
    function fitAllColumns() { for (let i = 0; i < 10; i++) fitColumn(i) }
    function resetColumnWidths() {
        table.clearColumnWidths()
        columnWidths = defaultColumnWidths.slice()
        table.forceLayout()
    }
    function toggleColumn(column) {
        if (column === titleColumn) return
        let hidden = hiddenColumns.slice()
        let at = hidden.indexOf(column)
        if (at >= 0) hidden.splice(at, 1); else hidden.push(column)
        hiddenColumns = hidden
        table.forceLayout()
    }
    readonly property bool hasSelection: desktop.model.counts.selected > 0
    readonly property bool hasRows: desktop.model.counts.visible > 0
    readonly property bool loaded: !!desktop.audio.title
    readonly property bool remoteView: !!desktop.view.source && !desktop.view.local
    readonly property bool folderView: !!desktop.folder.path && desktop.view.title === desktop.folder.path
    readonly property bool dialogOpen: dialog.visible || helpDialog.visible || messageLog.visible
    color: bg
    palette.window: bg
    palette.windowText: fg
    palette.base: panel
    palette.text: fg
    palette.button: panel
    palette.buttonText: fg
    palette.highlight: selection
    palette.highlightedText: selectionText
    // Basic controls use these roles for alternate rows, placeholders, arrows,
    // popup states and tooltips. Never inherit the host's opposite-theme colors.
    palette.alternateBase: alternate
    palette.placeholderText: muted
    palette.light: hoverSurface
    palette.midlight: hoverSurface
    palette.mid: pressedSurface
    // Basic draws primary buttons, progress fills, spinners and combo arrows in this role.
    palette.dark: accent
    palette.brightText: selectionText
    palette.shadow: "#000000"
    palette.toolTipBase: panel
    palette.toolTipText: fg
    palette.accent: accent
    palette.link: accent
    palette.linkVisited: accent
    palette.disabled.text: disabledFg
    palette.disabled.windowText: disabledFg
    palette.disabled.buttonText: disabledFg
    palette.disabled.placeholderText: disabledFg

    function clock(seconds) {
        let s = Math.max(0, Math.floor(seconds || 0))
        return Math.floor(s / 60) + ":" + (s % 60 < 10 ? "0" : "") + s % 60
    }
    function storeColor(name) {
        switch (name) {
        case "bandcamp": return dark ? "#7fb8e6" : "#1d5f9e"
        case "beatport": return dark ? "#8fe08f" : "#1f7a3a"
        case "gate": case "↓gate": return warning
        case "soundcloud": return dark ? "#f2a56b" : "#b4560a"
        case "no-link": return muted
        default: return fg
        }
    }
    // Keys go around the Camelot wheel in twelve hues; B (major) is a lighter shade than A.
    function camelotHue(code) { return ((parseInt(code) || 1) - 1) / 12 }
    function keyTint(code, alpha) {
        return code ? Qt.hsla(camelotHue(code), .72, code.endsWith("B") ? .6 : .48, alpha) : Qt.rgba(0, 0, 0, 0)
    }
    // Mixable with the playing track: same key, a step around the wheel, or its relative major/minor.
    function harmonic(a, b) {
        if (!a || !b) return false
        let na = parseInt(a), nb = parseInt(b)
        if (a.slice(-1) !== b.slice(-1)) return na === nb
        let d = Math.abs(na - nb)
        return d <= 1 || d === 11
    }
    function tempoMatch(bpm, other) { return bpm > 0 && other > 0 && Math.abs(bpm - other) <= other * .03 }
    function statusColor(status) {
        return status === "got" ? success : status === "skip" || status === "new" ? muted : status === "opened" ? warning : fg
    }
    function note(text, level) {
        messages = [(new Date()).toLocaleTimeString(Qt.locale(), Locale.ShortFormat) + (level === "error" ? " ! " : "   ") + text].concat(messages).slice(0, 50)
    }
    function playSelected() {
        if (hasSelection) desktop.action("play")
        else if (loaded) desktop.transport("toggle", 0)
    }
    function nudge(seconds) { if (loaded) desktop.transport("nudge", seconds) }
    function changeVolume(delta) {
        volume = Math.max(0, Math.min(1, volume + delta)); isMuted = false
        desktop.transport("volume", volume)
    }
    function toggleMute() { isMuted = !isMuted; desktop.transport("mute", 0) }
    function clearFilters() {
        if (hasSelection) desktop.model.clearSelection()
        else if (search.text) search.text = ""
        else { store.currentIndex = 0; hide.checked = false; filterTimer.restart() }
        table.forceActiveFocus()
    }
    function shortcutList() {
        let lines = []
        for (let m = 0; m < menuBar.count; m++) {
            let menu = menuBar.menuAt(m)
            lines.push("\n" + menu.title)
            for (let i = 0; i < menu.count; i++) {
                let action = menu.actionAt(i)
                if (action && action.shortcut) lines.push("  " + action.shortcut + "\t" + action.text)
            }
        }
        lines.push("\n" + qsTr("Track list"))
        lines.push("  Enter\t" + qsTr("Open links"), "  Delete\t" + qsTr("Remove from playlist"),
                   "  Esc\t" + qsTr("Clear selection, then search, then filters"), "  Ctrl+C\t" + qsTr("Copy artist and title"),
                   "  Ctrl+A\t" + qsTr("Select all"), "  Shift+↑/↓\t" + qsTr("Extend selection"), "  " + qsTr("Double click") + "\t" + qsTr("Play"))
        return lines.join("\n")
    }

    Component.onCompleted: {
        let s = desktop.settings()
        width = Math.min(Screen.width, Math.max(760, Number(s.width) || 1200))
        height = Math.min(Screen.height, Math.max(520, Number(s.height) || 800))
        languageCode = s.language === "pl" || s.language === "en" ? s.language : systemLanguage
        themeChoice = ["system", "light", "dark"].indexOf(s.theme) >= 0 ? s.theme : "system"
        if (Array.isArray(s.columnWidths) && s.columnWidths.length === 10)
            columnWidths = s.columnWidths.map(v => Math.max(40, Math.min(600, Number(v) || 100)))
        if (Array.isArray(s.hiddenColumns))
            hiddenColumns = s.hiddenColumns.map(Number).filter(v => Number.isInteger(v) && v >= 0 && v < 10 && v !== titleColumn)
        if (Array.isArray(s.columnOrder) && s.columnOrder.length === 10
                && [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].every(i => s.columnOrder.map(Number).indexOf(i) >= 0))
            applyColumnOrder(s.columnOrder.map(Number))
        sidebarWidth = Math.max(160, Math.min(400, Number(s.sidebarWidth) || 220))
        if (s.sidebarVisible === false) sidebarVisible = false
        if (s.animations === false) animations = false
        keyNotation = s.keyNotation === "classic" ? "classic" : "camelot"
        if (Number.isInteger(s.pulseOffset) && s.pulseOffset >= 0 && s.pulseOffset <= 400) pulseOffset = s.pulseOffset
        desktop.language(languageCode)
        table.forceActiveFocus()
    }
    onClosing: function(event) {
        event.accepted = false
        if (!shuttingDown) {
            shuttingDown = true
            for (let i = 0; i < 10; i++) if (i !== titleColumn && columnVisible(i)) columnWidths[i] = table.columnWidth(i)
            desktop.saveSettings({sidebarWidth: sidebar.width, sidebarVisible: sidebarVisible, width: width, height: height, language: languageCode,
                                  theme: themeChoice, columnWidths: columnWidths, hiddenColumns: hiddenColumns, columnOrder: columnOrder,
                                  keyNotation: keyNotation, animations: animations, pulseOffset: pulseOffset})
            desktop.close()
        }
    }
    function nextQuestion() {
        if (dialog.visible || pendingQuestions.length === 0) return
        let q = pendingQuestions.shift()
        dialog.ident = q.id; dialog.title = q.title; dialog.body = q.body; dialog.error = q.error || ""
        dialog.fields = q.fields; dialog.values = {}
        for (let f of q.fields) dialog.values[f.name] = f.value
        dialog.okText = q.ok || ""; dialog.info = !!q.info
        dialog.open()
    }
    Connections {
        target: desktop
        function onQuestion(q) { root.pendingQuestions.push(q); root.nextQuestion() }
        function onDismiss(id) {
            root.pendingQuestions = root.pendingQuestions.filter(q => q.id !== id)
            if (dialog.ident === id) dialog.close()
            Qt.callLater(root.nextQuestion)
        }
        function onNotice(text, level) {
            root.note(text, level)
            if (level === "error") root.errorMessage = text
        }
    }

    menuBar: MenuBar {
        AppMenu {
            title: qsTr("Library")
            Action { text: qsTr("Add playlist…"); shortcut: "A"; onTriggered: desktop.action("dig") }
            Action { text: qsTr("Add folder…"); shortcut: "Ctrl+O"; onTriggered: folderDialog.open() }
            Action { text: qsTr("Import profile playlists…"); onTriggered: desktop.action("profile") }
            Action { text: qsTr("Import saved summary…"); onTriggered: desktop.action("import_summary") }
            MenuSeparator {}
            Action { text: qsTr("Refresh"); shortcut: "R"; enabled: !desktop.busy && (root.remoteView || root.folderView); onTriggered: desktop.action("refresh") }
            Action { text: qsTr("Restore removed tracks"); enabled: root.remoteView; onTriggered: desktop.action("restore") }
            Action { text: qsTr("Delete playlist…"); shortcut: "Shift+X"; enabled: !!desktop.view.source; onTriggered: desktop.deletePlaylist(desktop.view.source) }
            MenuSeparator {}
            Action { text: qsTr("Pin folder"); enabled: root.folderView; onTriggered: desktop.action("pin") }
            Action { text: qsTr("Save local playlist…"); enabled: root.hasSelection && desktop.view.local; onTriggered: desktop.action("save_playlist") }
            Action { text: qsTr("Scan local library"); enabled: !desktop.busy; onTriggered: desktop.action("scan") }
            MenuSeparator {}
            Action { text: qsTr("Quit"); shortcut: "Ctrl+Q"; onTriggered: root.close() }
        }
        AppMenu {
            title: qsTr("Tracks")
            Action { text: qsTr("Open links"); shortcut: "O"; enabled: root.hasSelection; onTriggered: desktop.action("open") }
            Action { text: qsTr("Open all visible links"); shortcut: "Shift+O"; enabled: root.hasRows && !desktop.busy; onTriggered: desktop.action("open", true) }
            Action { text: qsTr("Download"); shortcut: "D"; enabled: root.hasSelection && !desktop.busy; onTriggered: desktop.action("download") }
            Action { text: qsTr("Download all visible"); shortcut: "Shift+D"; enabled: root.hasRows && !desktop.busy; onTriggered: desktop.action("download", true) }
            MenuSeparator {}
            Action { text: qsTr("Mark owned"); shortcut: "G"; enabled: root.hasSelection; onTriggered: desktop.mark("got") }
            Action { text: qsTr("Skip"); shortcut: "K"; enabled: root.hasSelection; onTriggered: desktop.mark("skip") }
            Action { text: qsTr("Reset status"); shortcut: "U"; enabled: root.hasSelection; onTriggered: desktop.mark("new") }
            Action { text: qsTr("Undo status change"); shortcut: "Ctrl+Z"; onTriggered: desktop.action("undo") }
            Action { text: qsTr("Remove from playlist…"); shortcut: "X"; enabled: root.hasSelection && root.remoteView; onTriggered: desktop.action("remove") }
        }
        AppMenu {
            title: qsTr("Playback")
            // Space always belongs to playback: the selected track if there is one (a different
            // track starts, the playing one toggles), otherwise the loaded track.
            Action { text: qsTr("Play / pause"); shortcut: "Space"; enabled: root.loaded || root.hasSelection; onTriggered: root.playSelected() }
            Action { text: qsTr("Stop"); shortcut: "Ctrl+W"; enabled: root.loaded; onTriggered: desktop.transport("stop", 0) }
            Action { text: qsTr("Previous track"); shortcut: "P"; enabled: root.loaded; onTriggered: desktop.step(-1) }
            Action { text: qsTr("Next track"); shortcut: "N"; enabled: root.loaded; onTriggered: desktop.step(1) }
            MenuSeparator {}
            Action { text: qsTr("Seek backward 10 s"); shortcut: "["; enabled: root.loaded; onTriggered: root.nudge(-10) }
            Action { text: qsTr("Seek forward 10 s"); shortcut: "]"; enabled: root.loaded; onTriggered: root.nudge(10) }
            Action { text: qsTr("Volume down"); shortcut: "-"; onTriggered: root.changeVolume(-.05) }
            Action { text: qsTr("Volume up"); shortcut: "="; onTriggered: root.changeVolume(.05) }
            Action { id: muteAction; text: qsTr("Mute"); shortcut: "M"; checkable: true; onTriggered: root.toggleMute() }
        }
        AppMenu {
            title: qsTr("Tools")
            Action { text: qsTr("Analyze BPM / key"); enabled: root.hasSelection && !desktop.busy; onTriggered: desktop.action("analyze") }
            Action { text: qsTr("Edit BPM / key…"); shortcut: "E"; enabled: desktop.model.counts.selected === 1; onTriggered: desktop.action("edit") }
            Action { text: qsTr("Analyze folder"); enabled: root.folderView && !desktop.busy; onTriggered: desktop.action("analyze_folder") }
            MenuSeparator {}
            Action { text: qsTr("Export audio…"); enabled: (root.hasSelection || desktop.view.local) && !desktop.busy; onTriggered: desktop.action("export", !root.hasSelection) }
            Action { text: qsTr("Resume export"); enabled: !desktop.busy; onTriggered: desktop.action("resume") }
            Action { text: qsTr("Delete files…"); enabled: root.hasSelection && !desktop.busy; onTriggered: desktop.action("delete_files") }
            MenuSeparator {}
            Action { text: qsTr("Export links…"); shortcut: "Shift+E"; enabled: root.hasRows; onTriggered: desktop.action("summary", !root.hasSelection) }
            Action { text: qsTr("Prepare cart / Beatport playlist"); shortcut: "C"; enabled: root.hasSelection && !desktop.busy; onTriggered: desktop.action("cart") }
            Action { text: qsTr("Prepare cart for all visible"); shortcut: "Shift+C"; enabled: root.hasRows && !desktop.busy; onTriggered: desktop.action("cart", true) }
        }
        AppMenu {
            title: qsTr("View")
            Action { text: qsTr("Search"); shortcut: "Ctrl+F"; onTriggered: { search.forceActiveFocus(); search.selectAll() } }
            Action { id: hideAction; text: qsTr("Hide handled"); shortcut: "H"; checkable: true; onTriggered: { hide.checked = !hide.checked; filterTimer.restart() } }
            Action { text: qsTr("Select all"); shortcut: "Ctrl+A"; onTriggered: desktop.model.selectAll() }
            MenuSeparator {}
            Action { id: sidebarAction; text: qsTr("Show sidebar"); shortcut: "Ctrl+B"; checkable: true; onTriggered: root.sidebarVisible = !root.sidebarVisible }
            AppMenu {
                title: qsTr("Language")
                ActionGroup { id: languageGroup }
                Action { text: "Polski"; checkable: true; checked: root.languageCode === "pl"; ActionGroup.group: languageGroup; onTriggered: { root.languageCode = "pl"; desktop.language("pl") } }
                Action { text: "English"; checkable: true; checked: root.languageCode === "en"; ActionGroup.group: languageGroup; onTriggered: { root.languageCode = "en"; desktop.language("en") } }
            }
            AppMenu {
                title: qsTr("Theme")
                ActionGroup { id: themeGroup }
                Action { text: qsTr("System"); checkable: true; checked: root.themeChoice === "system"; ActionGroup.group: themeGroup; onTriggered: root.themeChoice = "system" }
                Action { text: qsTr("Dark"); checkable: true; checked: root.themeChoice === "dark"; ActionGroup.group: themeGroup; onTriggered: root.themeChoice = "dark" }
                Action { text: qsTr("Light"); checkable: true; checked: root.themeChoice === "light"; ActionGroup.group: themeGroup; onTriggered: root.themeChoice = "light" }
            }
            AppMenu {
                title: qsTr("Key notation")
                ActionGroup { id: notationGroup }
                Action { text: qsTr("Camelot (8A)"); checkable: true; checked: root.keyNotation === "camelot"; ActionGroup.group: notationGroup; onTriggered: root.keyNotation = "camelot" }
                Action { text: qsTr("Classic (Am)"); checkable: true; checked: root.keyNotation === "classic"; ActionGroup.group: notationGroup; onTriggered: root.keyNotation = "classic" }
            }
            Action { id: animationsAction; text: qsTr("Animations"); checkable: true; onTriggered: root.animations = !root.animations }
            // Tuned by ear while a track plays: the speaker delay differs per machine.
            AppMenu {
                title: qsTr("Pulse timing")
                Action { text: qsTr("Delay: %1 ms").arg(root.pulseOffset); enabled: false }
                Action { text: qsTr("Pulse earlier (−10 ms)"); enabled: root.pulseOffset > 0; onTriggered: root.pulseOffset = Math.max(0, root.pulseOffset - 10) }
                Action { text: qsTr("Pulse later (+10 ms)"); enabled: root.pulseOffset < 400; onTriggered: root.pulseOffset = Math.min(400, root.pulseOffset + 10) }
                Action { text: qsTr("Reset"); onTriggered: root.pulseOffset = 70 }
            }
        }
        AppMenu {
            title: qsTr("Settings")
            Action { text: qsTr("Preferences…"); shortcut: "S"; onTriggered: desktop.action("settings") }
            MenuSeparator {}
            Action { text: qsTr("Sign in to SoundCloud"); enabled: !desktop.busy; onTriggered: desktop.action("login") }
            Action { text: qsTr("Store accounts"); enabled: !desktop.busy; onTriggered: desktop.action("store_login") }
            Action { text: qsTr("Sign out…"); onTriggered: desktop.action("logout") }
        }
        AppMenu {
            title: qsTr("Help")
            Action { text: qsTr("Keyboard shortcuts"); shortcut: "?"; onTriggered: { helpText.text = root.shortcutList(); helpDialog.open() } }
            Action { text: qsTr("Messages"); onTriggered: messageLog.open() }
            Action { text: qsTr("Diagnostics"); onTriggered: desktop.action("logs") }
        }
    }
    Binding { target: hideAction; property: "checked"; value: hide.checked }
    Binding { target: muteAction; property: "checked"; value: root.isMuted }
    Binding { target: sidebarAction; property: "checked"; value: root.sidebarVisible }
    Binding { target: animationsAction; property: "checked"; value: root.animations }
    Shortcut { sequence: "/"; enabled: !root.dialogOpen; onActivated: { search.forceActiveFocus(); search.selectAll() } }
    Shortcut { sequence: "Escape"; enabled: !root.dialogOpen; onActivated: root.clearFilters() }

    // Every menu shares one row layout: a fixed check column, the label, the
    // shortcut in the muted color and a submenu arrow. Basic never shows
    // shortcuts and indents only checkable items, which misaligned the labels.
    component AppMenuItem: MenuItem {
        id: menuItem
        property string keys: ""
        readonly property string shortcutText: keys || (action && action.shortcut ? String(action.shortcut) : "")
        // Plain Text measures synchronously, so the menu can size itself before it opens.
        implicitWidth: leftPadding + labelText.implicitWidth + (shortcutLabel.visible ? 28 + shortcutLabel.implicitWidth : 0) + rightPadding
        implicitHeight: 30
        // Menus do not collapse invisible entries on their own.
        height: visible ? implicitHeight : 0
        padding: 4; leftPadding: 32; rightPadding: subMenu ? 28 : 14; spacing: 0
        indicator: ColorImage {
            x: 10; y: (menuItem.height - height) / 2; width: 14; height: 14; sourceSize: Qt.size(14, 14)
            source: "icons/check.svg"; visible: menuItem.checkable && menuItem.checked
            color: menuItem.enabled ? root.fg : root.disabledFg
        }
        arrow: ColorImage {
            x: menuItem.width - width - 10; y: (menuItem.height - height) / 2; width: 12; height: 12; sourceSize: Qt.size(12, 12)
            source: "icons/chevron.svg"; visible: menuItem.subMenu; color: menuItem.enabled ? root.muted : root.disabledFg
        }
        contentItem: Item {
            implicitHeight: labelText.implicitHeight
            Text {
                id: labelText
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                width: Math.min(implicitWidth, parent.width - (shortcutLabel.visible ? shortcutLabel.implicitWidth + 28 : 0))
                text: menuItem.text; textFormat: Text.PlainText; elide: Text.ElideRight; font: menuItem.font
                color: menuItem.enabled ? root.fg : root.disabledFg
            }
            Text {
                id: shortcutLabel
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                visible: text.length > 0; text: menuItem.shortcutText; textFormat: Text.PlainText
                font.pixelSize: 12; color: menuItem.enabled ? root.muted : root.disabledFg
            }
        }
        background: Rectangle { x: 3; width: menuItem.width - 6; height: menuItem.height; radius: 3; color: menuItem.highlighted ? root.hoverSurface : "transparent" }
    }
    component AppSeparator: MenuSeparator { height: visible ? implicitHeight : 0 }
    component AppMenu: Menu {
        id: appMenu
        delegate: AppMenuItem {}
        topPadding: 4; bottomPadding: 4
        background: Rectangle { implicitWidth: 200; color: root.panel; border.color: root.alternate; radius: 4 }
        // Retranslation can change label widths after the menu measured itself.
        onAboutToShow: {
            let widest = 200
            for (let i = 0; i < count; i++) { let item = itemAt(i); if (item) widest = Math.max(widest, item.implicitWidth) }
            width = widest + leftPadding + rightPadding
        }
    }
    // Icon buttons share one flat look: no frame at rest, a soft hover surface.
    component FlatButton: ToolButton {
        id: flatButton
        property string keys: ""
        display: AbstractButton.IconOnly
        icon.width: 14; icon.height: 14; icon.color: enabled ? root.fg : root.disabledFg
        padding: 4
        background: Rectangle { radius: 4; color: flatButton.down ? root.pressedSurface : flatButton.hovered ? root.hoverSurface : "transparent" }
        Accessible.name: text
        ToolTip.visible: hovered; ToolTip.text: text + (keys ? " (" + keys + ")" : "")
    }
    component SectionHeader: RowLayout {
        id: sectionHeader
        property alias title: sectionTitle.text
        property string buttonText: ""
        property string buttonName: ""
        signal add()
        Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 2; spacing: 0
        Label {
            id: sectionTitle
            Layout.fillWidth: true; textFormat: Text.PlainText; elide: Text.ElideRight
            font.bold: true; font.pixelSize: 11; font.letterSpacing: 1; font.capitalization: Font.AllUppercase; color: root.muted
        }
        FlatButton {
            objectName: sectionHeader.buttonName; text: sectionHeader.buttonText
            implicitWidth: 26; implicitHeight: 26; icon.source: "icons/plus.svg"; icon.width: 12; icon.height: 12; icon.color: root.muted
            onClicked: sectionHeader.add()
        }
    }
    // Dialogs share the panel surface, a compact title and right-aligned, normal-sized buttons.
    component AppDialog: Dialog {
        id: appDialog
        modal: true
        padding: 16; topPadding: 8
        background: Rectangle { color: root.panel; border.color: root.alternate; radius: 6 }
        header: Label {
            text: appDialog.title; visible: appDialog.title.length > 0
            textFormat: Text.PlainText; elide: Text.ElideRight
            font.bold: true; font.pixelSize: 15; color: root.fg
            padding: 16; bottomPadding: 8
        }
        footer: DialogButtonBox {
            visible: count > 0
            alignment: Qt.AlignRight; spacing: 8
            padding: 16; topPadding: 4
            background: null
            delegate: Button { implicitWidth: Math.max(96, implicitContentWidth + 32) }
        }
        onAboutToShow: root.localizeButtons(appDialog)
    }
    // One layer of waveform bars; the played layer is the same painting clipped to the progress.
    component WaveformBars: Canvas {
        id: bars
        property bool played: false
        property bool glow: false
        onPaint: waveform.paintBars(getContext("2d"), width, height, played, glow)
        // A resize repaints once it settles; a window or sidebar drag would otherwise paint every frame.
        Timer { id: settle; interval: 50; onTriggered: bars.requestPaint() }
        onWidthChanged: settle.restart()
        onHeightChanged: settle.restart()
        onVisibleChanged: requestPaint()
        Connections { target: waveform; function onLevelsChanged() { requestPaint() } }
        Connections { target: root; function onDarkChanged() { requestPaint() } }
    }
    // A rounded label for BPM and key; a key takes its Camelot hue, a match with the playing track gets a ring.
    component Chip: Rectangle {
        id: chip
        property alias text: chipLabel.text
        property string code: ""
        property bool match: false
        property bool selected: false
        implicitWidth: chipLabel.implicitWidth + 14; implicitHeight: 22; radius: 11
        color: selected ? root.panel : code ? root.keyTint(code, root.dark ? .3 : .22) : root.alternate
        border.width: match ? 2 : code ? 1 : 0
        border.color: match ? (selected ? root.accent2 : root.accent) : root.keyTint(code, .8)
        Label {
            id: chipLabel
            anchors.centerIn: parent; textFormat: Text.PlainText; font.pixelSize: 12; font.features: root.tabular
            font.bold: chip.match; color: root.fg
        }
        ToolTip.visible: match && chipHover.hovered; ToolTip.text: qsTr("Mixes with the playing track")
        HoverHandler { id: chipHover }
    }
    // Generated artwork, never fetched: a record on a gradient, both picked from the track key.
    // The record turns while the track plays.
    component Cover: Rectangle {
        id: cover
        property string seed: ""
        property bool spinning: false
        readonly property int hash: {
            let h = 7
            for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0
            return Math.abs(h)
        }
        readonly property var tones: [root.accent, root.accent2, "#4368ba", "#b23755", root.silver]
        function tone(shift) { return tones[((hash >> shift) % 5 + shift) % 5] }
        radius: 6; clip: true
        gradient: Gradient {
            orientation: cover.hash % 2 ? Gradient.Vertical : Gradient.Horizontal
            GradientStop { position: 0; color: cover.tone(0) }
            GradientStop { position: 1; color: cover.tone(3) }
        }
        // The backdrop glows on each kick.
        Rectangle {
            objectName: "coverGlow"
            anchors.fill: parent; radius: parent.radius; opacity: root.glow
            gradient: Gradient {
                orientation: cover.hash % 2 ? Gradient.Vertical : Gradient.Horizontal
                GradientStop { position: 0; color: root.peakColor(cover.tone(0)) }
                GradientStop { position: 1; color: root.peakColor(cover.tone(3)) }
            }
        }
        Item {
            id: disc
            width: parent.width * .84; height: width; anchors.centerIn: parent
            // Near-black vinyl; the label is darkened so it does not outshine the record.
            Rectangle { anchors.fill: parent; radius: width / 2; color: "#0c0c0e" }
            Rectangle { anchors.centerIn: parent; width: disc.width * .34; height: width; radius: width / 2; color: Qt.darker(cover.tone(5), 1.25) }
            // A barcode of the track: radial strokes and dots from the label to the rim, laid out
            // from the key hash. Painted once per track; turning the record costs no repaint.
            Canvas {
                objectName: "trackCode"
                anchors.fill: parent
                property int code: cover.hash
                onCodeChanged: requestPaint()
                onWidthChanged: requestPaint()
                onPaint: {
                    let ctx = getContext("2d")
                    ctx.reset()
                    let state = code | 0
                    function random() {  // mulberry32: the same track always draws the same code
                        state = (state + 0x6D2B79F5) | 0
                        let t = Math.imul(state ^ (state >>> 15), 1 | state)
                        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
                        return ((t ^ (t >>> 14)) >>> 0) / 4294967296
                    }
                    let centre = width / 2, inner = width * .19, outer = width * .48
                    // Spans along the radius, 0 at the label and 1 at the rim; the empty one is dots only.
                    let patterns = [[[0, 1]], [[0, .5]], [[.5, 1]], [[.3, .7]], [[0, 1 / 3], [2 / 3, 1]], []]
                    ctx.strokeStyle = ctx.fillStyle = Qt.rgba(1, 1, 1, .6)
                    ctx.lineWidth = Math.max(1, width / 110); ctx.lineCap = "round"
                    // Evenly spread around the record, each nudged within its own slot, so no part
                    // of it is left bare while no two tracks share a layout.
                    let count = 18 + Math.floor(random() * 10), turn = random() * Math.PI * 2
                    for (let i = 0; i < count; i++) {
                        let angle = turn + (i + .15 + random() * .7) / count * Math.PI * 2, dx = Math.cos(angle), dy = Math.sin(angle)
                        let pattern = patterns[Math.floor(random() * patterns.length)]
                        for (let span of pattern) {
                            let from = inner + span[0] * (outer - inner), to = inner + span[1] * (outer - inner)
                            ctx.beginPath(); ctx.moveTo(centre + dx * from, centre + dy * from); ctx.lineTo(centre + dx * to, centre + dy * to); ctx.stroke()
                        }
                        if (pattern.length === 0 || random() < .3) {
                            for (let dots = 1 + Math.floor(random() * 3); dots > 0; dots--) {
                                let radius = inner + random() * (outer - inner)
                                ctx.beginPath(); ctx.arc(centre + dx * radius, centre + dy * radius, ctx.lineWidth * 1.2, 0, Math.PI * 2); ctx.fill()
                            }
                        }
                    }
                }
            }
            NumberAnimation on rotation {
                from: 0; to: 360; duration: 4000; loops: Animation.Infinite
                running: root.motion; paused: running && !cover.spinning
            }
        }
    }
    // The same hue, more saturated and lighter: a kick makes a colour glow without
    // washing it out. A red (from 70 % of linear R+G+B) only lightens, and less, so
    // the bordeaux end stays under the WCAG 2.3.1 red-flash threshold.
    function peakColor(value) {
        let color = Qt.color(value)  // Artwork tones may be colour names as text.
        let linear = c => c <= .04045 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4)
        let r = linear(color.r), g = linear(color.g), b = linear(color.b)
        if (r / Math.max(1e-6, r + g + b) >= .7)
            return Qt.hsla(color.hslHue, color.hslSaturation, Math.min(1, color.hslLightness + (dark ? .02 : .03)), 1)
        return Qt.hsla(color.hslHue, Math.min(1, color.hslSaturation + .3), Math.min(1, color.hslLightness + (dark ? .06 : .08)), 1)
    }
    function bpmText(bpm) { return String(Math.round(bpm * 10) / 10) }
    component FolderIcon: ColorImage {
        property bool selected: false
        width: 16; height: 16; sourceSize: Qt.size(16, 16); source: "icons/folder.svg"
        color: selected ? root.selectionText : root.muted
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 0; spacing: 0
        enabled: desktop.ready && !root.shuttingDown
        SplitView {
            Layout.fillWidth: true; Layout.fillHeight: true
            orientation: Qt.Horizontal
            // Draggable but invisible: the columns separate themselves by content.
            handle: Rectangle { implicitWidth: 6; implicitHeight: 6; color: "transparent" }
            SplitView {
                id: sidebar
                visible: root.sidebarVisible
                orientation: Qt.Vertical
                handle: Rectangle { implicitWidth: 6; implicitHeight: 6; color: "transparent" }
                SplitView.preferredWidth: root.sidebarWidth; SplitView.minimumWidth: 160
                SplitView.maximumWidth: Math.max(160, root.width - 520)
                ColumnLayout {
                    SplitView.preferredHeight: parent.height / 2; SplitView.minimumHeight: 120
                    spacing: 2
                    SectionHeader { title: qsTr("Playlists"); Layout.topMargin: 4; buttonText: qsTr("Add playlist"); onAdd: desktop.action("dig") }
                    Item {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    ListView {
                        id: playlistList
                        anchors.fill: parent; clip: true
                        model: desktop.playlists
                        delegate: ItemDelegate {
                            id: playlistItem
                            objectName: "playlist-" + modelData.source
                            background: Rectangle { color: playlistItem.highlighted ? root.selection : playlistItem.hovered ? root.hoverSurface : "transparent" }
                            required property var modelData
                            width: ListView.view.width; height: 28
                            padding: 0; leftPadding: 10; rightPadding: 6
                            highlighted: modelData.source === desktop.view.source
                            // A playlist still importing is listed at once, with a spinner; it opens when saved.
                            readonly property bool pending: !!modelData.pending
                            contentItem: RowLayout {
                                spacing: 6
                                Label { visible: !playlistItem.pending; text: modelData.source.startsWith("local-playlist:") ? "▣" : "☁"; color: playlistItem.highlighted ? root.selectionText : root.muted }
                                BusyIndicator {
                                    visible: playlistItem.pending; running: visible
                                    implicitWidth: 16; implicitHeight: 16; padding: 0
                                    palette.dark: playlistItem.highlighted ? root.selectionText : root.accent
                                }
                                Label { Layout.fillWidth: true; text: modelData.title; textFormat: Text.PlainText; elide: Text.ElideRight; color: playlistItem.highlighted ? root.selectionText : root.fg }
                            }
                            onClicked: if (!pending) desktop.load(modelData.source)
                            onPressAndHold: { playlistMenu.source = modelData.source; playlistMenu.popup() }
                            TapHandler { acceptedButtons: Qt.RightButton; onTapped: { playlistMenu.source = playlistItem.modelData.source; playlistMenu.popup() } }
                        }
                        ScrollBar.vertical: ScrollBar {}
                    }
                    Label { anchors.centerIn: parent; width: parent.width - 16; visible: playlistList.count === 0; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: root.muted; text: qsTr("No playlists yet. Add a SoundCloud link to start digging.") }
                    }
                }
                ColumnLayout {
                    SplitView.minimumHeight: 120
                    spacing: 2
                    SectionHeader {
                        title: qsTr("Local files"); Layout.topMargin: 4
                        buttonName: "addFolder"; buttonText: qsTr("Add folder…"); onAdd: folderDialog.open()
                    }
                    ScrollView {
                        id: folderScroll
                        Layout.fillWidth: true; Layout.fillHeight: true
                        contentWidth: availableWidth
                        Column {
                            width: folderScroll.availableWidth
                            Repeater {
                                model: desktop.directoryRoots
                                Column {
                                    id: folderRoot
                                    required property var modelData
                                    width: parent.width
                                    property bool expanded: false
                                    readonly property bool expandable: {
                                        // Refresh after Qt's background directory enumeration.
                                        const revision = desktop.directoryModel.revision
                                        return desktop.directoryHasChildren(modelData.path)
                                    }
                                    onExpandableChanged: if (!expandable) expanded = false
                                    ItemDelegate {
                                        id: rootItem
                                        objectName: "root-" + folderRoot.modelData.kind
                                        width: parent.width; height: 28
                                        padding: 0; leftPadding: 6; rightPadding: 6
                                        highlighted: root.folderView && desktop.folder.path === folderRoot.modelData.path
                                        background: Rectangle { color: rootItem.highlighted ? root.selection : rootItem.hovered ? root.hoverSurface : "transparent" }
                                        contentItem: RowLayout {
                                            spacing: 4
                                            FlatButton {
                                                objectName: "expand-" + folderRoot.modelData.kind
                                                text: folderRoot.expanded ? qsTr("Collapse") : qsTr("Expand")
                                                icon.source: "icons/chevron.svg"; icon.width: 10; icon.height: 10
                                                icon.color: rootItem.highlighted ? root.selectionText : root.muted
                                                rotation: folderRoot.expanded ? 90 : 0
                                                enabled: folderRoot.expandable
                                                opacity: enabled ? 1 : 0
                                                implicitWidth: 20; implicitHeight: 20; padding: 0
                                                ToolTip.visible: false
                                                Accessible.ignored: !enabled
                                                Accessible.name: text + " " + folderRoot.modelData.path
                                                onClicked: folderRoot.expanded = !folderRoot.expanded
                                            }
                                            FolderIcon { selected: rootItem.highlighted }
                                            Label {
                                                Layout.fillWidth: true
                                                text: folderRoot.modelData.kind === "downloads" ? qsTr("Downloads")
                                                    : folderRoot.modelData.kind === "music" ? qsTr("Music")
                                                    : folderRoot.modelData.path.split(/[\\/]/).filter(p => p).pop() || folderRoot.modelData.path
                                                textFormat: Text.PlainText; elide: Text.ElideMiddle
                                                color: rootItem.highlighted ? root.selectionText : root.fg
                                            }
                                        }
                                        ToolTip.visible: hovered; ToolTip.text: folderRoot.modelData.path
                                        onClicked: desktop.openFolder(folderRoot.modelData.path, 0)
                                    }
                                    TreeView {
                                        id: directoryTree
                                        objectName: "directoryTree-" + folderRoot.modelData.kind
                                        width: parent.width
                                        height: visible ? Math.min(contentHeight, Math.max(96, folderScroll.availableHeight - 64)) : 0
                                        visible: folderRoot.expanded && folderRoot.expandable
                                        clip: true; model: desktop.directoryModel
                                        rootIndex: desktop.directoryIndex(folderRoot.modelData.path)
                                        editTriggers: TableView.NoEditTriggers
                                        selectionBehavior: TableView.SelectRows
                                        selectionModel: ItemSelectionModel { model: desktop.directoryModel }
                                        columnWidthProvider: column => column === 0 ? width : 0
                                        delegate: TreeViewDelegate {
                                            id: directoryDelegate
                                            objectName: "directory-" + fileName
                                            required property string fileName
                                            required property string filePath
                                            implicitWidth: directoryTree.width; implicitHeight: 28
                                            // Highlight follows the loaded view, not the tree's own selection,
                                            // so opening a playlist clears the folder highlight.
                                            highlighted: root.folderView && desktop.samePath(desktop.folder.path, filePath)
                                            // Children sit one step right of the root row's chevron and share its icon.
                                            leftMargin: 24; indentation: 16; spacing: 4
                                            indicator: Item {
                                                x: directoryDelegate.leftMargin + directoryDelegate.depth * directoryDelegate.indentation
                                                y: (directoryDelegate.height - height) / 2
                                                implicitWidth: 16; implicitHeight: 28
                                                ColorImage {
                                                    anchors.centerIn: parent; width: 10; height: 10; sourceSize: Qt.size(10, 10)
                                                    source: "icons/chevron.svg"; rotation: directoryDelegate.expanded ? 90 : 0
                                                    color: directoryDelegate.highlighted ? root.selectionText : root.muted
                                                }
                                            }
                                            background: Rectangle { color: directoryDelegate.highlighted ? root.selection : directoryDelegate.hovered ? root.hoverSurface : root.bg }
                                            contentItem: RowLayout {
                                                spacing: 6
                                                FolderIcon { selected: directoryDelegate.highlighted }
                                                Label { Layout.fillWidth: true; text: directoryDelegate.fileName; textFormat: Text.PlainText; elide: Text.ElideRight; color: directoryDelegate.highlighted ? root.selectionText : root.fg }
                                            }
                                            palette.windowText: highlighted ? root.selectionText : root.fg
                                            onExpandedChanged: if (expanded) Qt.callLater(() => { if (expanded) desktop.expandDirectory(filePath) })
                                            Accessible.name: fileName
                                            onClicked: desktop.openFolder(filePath, 0)
                                        }
                                        Keys.onReturnPressed: if (currentRow >= 0) desktop.openDirectory(index(currentRow, 0))
                                        ScrollBar.vertical: ScrollBar {}
                                    }
                                }
                            }
                        }
                    }
                    RowLayout {
                        visible: root.folderView && desktop.folder.total > 250
                        Layout.leftMargin: 6; Layout.rightMargin: 6; spacing: 0
                        FlatButton { icon.source: "icons/chevron.svg"; icon.width: 10; icon.height: 10; icon.color: enabled ? root.muted : root.disabledFg; rotation: 180; implicitWidth: 24; implicitHeight: 24; text: qsTr("Previous page"); enabled: desktop.folder.offset > 0; onClicked: desktop.openFolder(desktop.folder.path, Math.max(0, desktop.folder.offset-250)) }
                        Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; color: root.muted; font.pixelSize: 12; text: (desktop.folder.offset + 1) + "–" + Math.min(desktop.folder.offset + 250, desktop.folder.total) + " / " + desktop.folder.total }
                        FlatButton { icon.source: "icons/chevron.svg"; icon.width: 10; icon.height: 10; icon.color: enabled ? root.muted : root.disabledFg; implicitWidth: 24; implicitHeight: 24; text: qsTr("Next page"); enabled: desktop.folder.offset + 250 < desktop.folder.total; onClicked: desktop.openFolder(desktop.folder.path, desktop.folder.offset+250) }
                    }
                }
            }
            ColumnLayout {
                SplitView.fillWidth: true; SplitView.minimumWidth: 0
            Rectangle {
                id: player
                // The height switches at once: growing it frame by frame resized the cover and every
                // waveform canvas on each frame. The artwork and waveform fade in instead.
                Layout.fillWidth: true; Layout.preferredHeight: root.loaded ? 184 : 52
                color: root.panel; radius: 6
                RowLayout {
                    anchors.fill: parent; anchors.margins: 10; spacing: 12
                    Cover {
                        objectName: "cover"
                        // Transport controls come first; the artwork only takes room the panel can spare.
                        opacity: root.loaded ? 1 : 0
                        Behavior on opacity { enabled: root.motion; NumberAnimation { duration: 180 } }
                        visible: root.loaded && opacity > 0 && player.width >= 640
                        Layout.fillHeight: true; Layout.preferredWidth: height
                        seed: desktop.audioKey
                        spinning: desktop.playing
                    }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0; spacing: 6
                    RowLayout {
                        visible: root.loaded; spacing: 6; Layout.minimumWidth: 0
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 0
                            Label {
                                objectName: "nowPlayingTitle"
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                text: desktop.nowPlaying.name || desktop.audio.title || ""
                                textFormat: Text.PlainText; elide: Text.ElideRight; font.pixelSize: 17; font.bold: true
                            }
                            Label {
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                visible: text.length > 0; text: desktop.nowPlaying.artist || ""
                                textFormat: Text.PlainText; elide: Text.ElideRight; color: root.silver
                            }
                        }
                        Chip { visible: (desktop.nowPlaying.bpm || 0) > 0; text: root.bpmText(desktop.nowPlaying.bpm || 0) + " BPM" }
                        Chip {
                            visible: !!desktop.nowPlaying.camelot
                            code: desktop.nowPlaying.camelot || ""
                            text: (root.keyNotation === "camelot" ? desktop.nowPlaying.camelot : desktop.nowPlaying.classicKey) || ""
                        }
                    }
                    Item {
                        id: waveform; objectName: "waveform"
                        opacity: root.loaded ? 1 : 0
                        Behavior on opacity { enabled: root.motion; NumberAnimation { duration: 180 } }
                        visible: root.loaded && opacity > 0
                        Layout.fillWidth: true; Layout.fillHeight: true
                        property var samples: desktop.waveform
                        property real position: desktop.audio.position || 0
                        readonly property real duration: desktop.audio.duration || 0
                        // Recomputed when the bar count changes, not on every pixel of a resize.
                        readonly property int columns: Math.max(1, Math.floor(width / 3))
                        property var levels: desktop.waveformLevels(samples, columns)
                        // The bars are painted once per waveform; progress only moves a clip edge and the cursor, so
                        // position ticks and a drag cost no repaint. While scrubbing, the pointer's time
                        // shows; after release the target shows until the backend confirms it (or 1.5 s pass).
                        property real scrub: -1
                        property real pending: -1
                        // While audio plays with motion on, the playhead follows the interpolated clock of heard audio.
                        readonly property real heard: root.playhead >= 0 ? root.playhead : position
                        readonly property real fraction: scrub >= 0 ? scrub : pending >= 0 ? pending : duration > 0 ? Math.min(1, heard / duration) : 0
                        onPositionChanged: if (pending >= 0 && Math.abs(position / Math.max(1, duration) - pending) < 0.02) pending = -1
                        // A new waveform rises from the bottom.
                        transform: Scale { id: waveformGrowth; origin.y: waveform.height }
                        NumberAnimation { id: waveformRise; target: waveformGrowth; property: "yScale"; from: 0; to: 1; duration: 450; easing.type: Easing.OutCubic }
                        // Only a new track's waveform rises; the old → empty → new handover must not replay it.
                        property string risen: ""
                        onSamplesChanged: if (root.motion && samples.length && desktop.waveformKey !== risen) {
                            risen = desktop.waveformKey
                            waveformRise.restart()
                        }
                        Timer { id: pendingTimer; interval: 1500; onTriggered: waveform.pending = -1 }
                        function paintBars(ctx, width, height, played, glow) {
                            ctx.reset()
                            // Bordeaux at the foot of every bar rising into blue at the top of the panel;
                            // the glow layer is the same gradient at its peak colours.
                            let gradient = ctx.createLinearGradient(0, height, 0, 0)
                            gradient.addColorStop(0, glow ? root.peakColor(root.accent2) : root.accent2)
                            gradient.addColorStop(1, glow ? root.peakColor(root.accent) : root.accent)
                            ctx.fillStyle = gradient
                            ctx.globalAlpha = played ? 1 : root.dark ? .38 : .45
                            if (glow) {  // Light spills around the bars at the peak.
                                ctx.shadowBlur = 12
                                let halo = root.peakColor(root.accent)
                                ctx.shadowColor = Qt.rgba(halo.r, halo.g, halo.b, .9)
                            }
                            let count = levels.length
                            for (let i = 0; i < count; i++) {
                                let h = Math.max(1, levels[i] * (height - 2))
                                ctx.fillRect(i * width / count, height - h, Math.max(1, width / count - 1), h)
                            }
                        }
                        // The unplayed region stays in shadow and never pulses.
                        WaveformBars { objectName: "unplayedBars"; anchors.fill: parent }
                        // Only the played region crossfades to its peak colours on a kick.
                        Item {
                            clip: true
                            anchors { top: parent.top; bottom: parent.bottom; left: parent.left }
                            width: waveform.fraction * waveform.width
                            WaveformBars { objectName: "playedBars"; width: waveform.width; height: waveform.height; played: true }
                            // The opaque played base keeps its normal colours under the flash.
                            WaveformBars {
                                objectName: "kickBars"; width: waveform.width; height: waveform.height; played: true; glow: true
                                opacity: root.glow
                            }
                        }
                        // Subpixel position: the playhead slides instead of stepping a pixel at a time.
                        Rectangle {
                            objectName: "cursor"; antialiasing: true
                            x: Math.min(waveform.width - 2, waveform.fraction * waveform.width); width: 2; height: parent.height; color: root.fg
                        }
                        MouseArea {
                            id: waveformMouse
                            anchors.fill: parent; hoverEnabled: true
                            function fractionAt(x) { return Math.max(0, Math.min(1, x / width)) }
                            onPressed: mouse => waveform.scrub = fractionAt(mouse.x)
                            onPositionChanged: mouse => { if (pressed) waveform.scrub = fractionAt(mouse.x) }
                            onCanceled: waveform.scrub = -1
                            onReleased: mouse => {
                                let target = fractionAt(mouse.x)
                                waveform.scrub = -1
                                waveform.pending = target; pendingTimer.restart()
                                desktop.transport("seek", target * waveform.duration)
                            }
                            ToolTip.visible: containsMouse; ToolTip.delay: 300
                            ToolTip.text: root.clock(mouseX / width * waveform.duration)
                        }
                        Accessible.role: Accessible.Slider
                        Accessible.name: qsTr("Waveform")
                    }
                    RowLayout {
                        Button { text: qsTr("Previous track"); enabled: root.loaded; display: AbstractButton.IconOnly; implicitWidth: 40; icon.source: "icons/previous.svg"; icon.color: palette.buttonText; Accessible.name: text; ToolTip.visible: hovered; ToolTip.text: text + " (P)"; onClicked: desktop.step(-1) }
                        Button {
                            id: playButton
                            objectName: "playPause"
                            implicitWidth: 40
                            enabled: root.loaded || root.hasSelection
                            text: root.pauseTarget ? qsTr("Pause") : qsTr("Play")
                            display: AbstractButton.IconOnly
                            icon.source: root.pauseTarget ? "icons/pause.svg" : "icons/play.svg"
                            icon.color: palette.buttonText; icon.width: 20; icon.height: 20
                            onIconChanged: if (root.motion) playPop.restart()
                            SequentialAnimation {
                                id: playPop
                                NumberAnimation { target: playButton; property: "scale"; to: .82; duration: 70 }
                                NumberAnimation { target: playButton; property: "scale"; to: 1; duration: 180; easing.type: Easing.OutBack }
                            }
                            Accessible.name: text
                            ToolTip.visible: hovered; ToolTip.text: text + " (Space)"
                            onClicked: root.playSelected()
                        }
                        Button { text: qsTr("Next track"); enabled: root.loaded; display: AbstractButton.IconOnly; implicitWidth: 40; icon.source: "icons/next.svg"; icon.color: palette.buttonText; Accessible.name: text; ToolTip.visible: hovered; ToolTip.text: text + " (N)"; onClicked: desktop.step(1) }
                        Button { text: qsTr("Stop"); enabled: root.loaded; display: AbstractButton.IconOnly; implicitWidth: 40; icon.source: "icons/stop.svg"; icon.color: palette.buttonText; Accessible.name: text; ToolTip.visible: hovered; ToolTip.text: text + " (Ctrl+W)"; onClicked: desktop.transport("stop", 0) }
                        Label { visible: root.loaded; text: root.clock(desktop.audio.position) + " / " + root.clock(desktop.audio.duration); color: root.muted; font.features: root.tabular }
                        Item { visible: root.loaded; Layout.fillWidth: true; Layout.minimumWidth: 0 }
                        Label { visible: !root.loaded; text: root.hasSelection ? qsTr("Press Space to play the selected track") : qsTr("Select a track to play"); color: root.muted; elide: Text.ElideRight; Layout.fillWidth: true; Layout.minimumWidth: 0 }
                        // A speaker, struck through while muted; the words stay for tooltips and screen readers.
                        ToolButton {
                            objectName: "muteButton"
                            text: root.isMuted ? qsTr("Unmute") : qsTr("Mute")
                            display: AbstractButton.IconOnly
                            icon.source: root.isMuted ? "icons/volume-muted.svg" : "icons/volume.svg"
                            icon.color: palette.buttonText; icon.width: 20; icon.height: 20
                            implicitWidth: 40
                            Accessible.name: text; ToolTip.visible: hovered; ToolTip.text: text + " (M)"
                            onClicked: root.toggleMute()
                        }
                        Slider {
                            objectName: "volumeSlider"
                            from: 0; to: 1; value: root.volume
                            Layout.fillWidth: true; Layout.minimumWidth: 50; Layout.preferredWidth: 100; Layout.maximumWidth: 140
                            onMoved: { root.volume = value; root.isMuted = false; desktop.transport("volume", value) }
                            Accessible.name: qsTr("Volume")
                            ToolTip.visible: hovered || pressed; ToolTip.text: qsTr("Volume") + " " + Math.round(value * 100) + "%"
                        }
                    }
                }
                }
            }
                TextField {
                    id: search; objectName: "trackSearch"; placeholderText: qsTr("Search artist, title, genre, tag or label  ( / )"); Layout.fillWidth: true; Layout.minimumWidth: 0
                    onTextChanged: filterTimer.restart()
                    Keys.onReturnPressed: table.forceActiveFocus()
                    Keys.onDownPressed: table.forceActiveFocus()
                    Accessible.name: qsTr("Search tracks")
                }
                // Actions for the selection lead the row while tracks are selected; the filters follow.
                RowLayout {
                    id: toolbar
                    Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                    // Below this width the action buttons keep their icons and tooltips only.
                    readonly property bool compact: width < 1040
                    RowLayout {
                        id: trackActions
                        objectName: "trackActions"
                        // Present as soon as a view is loaded; entries that need a selection are disabled without one.
                        visible: !!desktop.view.title
                        Layout.minimumWidth: 0; spacing: 2
                        component ActionButton: FlatButton {
                            display: toolbar.compact ? AbstractButton.IconOnly : AbstractButton.TextBesideIcon
                            icon.width: 16; icon.height: 16
                            implicitHeight: 32; padding: 6; spacing: 6
                        }
                        ActionButton { text: qsTr("Open links"); keys: "O"; icon.source: "icons/link.svg"; enabled: root.hasSelection; onClicked: desktop.action("open") }
                        ActionButton { text: qsTr("Download"); keys: "D"; icon.source: "icons/download.svg"; enabled: root.hasSelection && !desktop.busy; onClicked: desktop.action("download") }
                        ActionButton { text: qsTr("Mark owned"); keys: "G"; icon.source: "icons/owned.svg"; enabled: root.hasSelection; onClicked: desktop.mark("got") }
                        ActionButton { text: qsTr("Skip"); keys: "K"; icon.source: "icons/skip.svg"; enabled: root.hasSelection; onClicked: desktop.mark("skip") }
                        ActionButton { text: qsTr("Analyze BPM / key"); icon.source: "icons/analyze.svg"; visible: !!desktop.view.local; enabled: root.hasSelection && !desktop.busy; onClicked: desktop.action("analyze") }
                        ActionButton {
                            id: moreActions
                            text: qsTr("More actions"); icon.source: "icons/more.svg"; display: AbstractButton.IconOnly
                            onClicked: { contextMenu.fromToolbar = true; contextMenu.popup(moreActions, 0, moreActions.height) }
                        }
                    }
                    Rectangle { visible: trackActions.visible; Layout.preferredWidth: 1; Layout.preferredHeight: 22; color: root.alternate }
                    ComboBox {
                        id: store; objectName: "storeFilter"
                        visible: !desktop.view.local
                        implicitWidth: 190
                        property var stores: desktop.model.stores
                        model: [qsTr("All stores")].concat(stores.map(s => s.name + " · " + s.count))
                        onActivated: filterTimer.restart()
                        delegate: ItemDelegate {
                            id: storeOption
                            required property int index
                            required property string modelData
                            width: ListView.view.width
                            text: modelData
                            highlighted: store.highlightedIndex === index
                            contentItem: Label {
                                text: storeOption.text; textFormat: Text.PlainText
                                color: storeOption.highlighted ? root.selectionText : root.fg
                            }
                            background: Rectangle { color: storeOption.highlighted ? root.selection : root.panel }
                        }
                    }
                    CheckBox { id: hide; text: qsTr("Hide handled"); onToggled: filterTimer.restart(); ToolTip.visible: hovered; ToolTip.text: qsTr("Hide owned and skipped tracks (H)") }
                    Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
                }
                Timer { id: filterTimer; interval: 120; onTriggered: desktop.model.filter(search.text, store.currentIndex > 0 && store.stores[store.currentIndex - 1] ? store.stores[store.currentIndex - 1].name : "", hide.checked) }
                HorizontalHeaderView {
                    id: header; syncView: table; Layout.fillWidth: true; clip: true
                    resizableColumns: true; movableColumns: true
                    delegate: Rectangle {
                        required property string display
                        required property int index
                        implicitHeight: 32; color: root.panel
                        Label {
                            anchors.fill: parent; anchors.margins: 5; textFormat: Text.PlainText; color: root.muted; elide: Text.ElideRight
                            horizontalAlignment: [4, 6, 8].indexOf(index) >= 0 ? Text.AlignRight : Text.AlignLeft
                            text: display + (desktop.model.sortColumn === index ? (desktop.model.sortReverse ? " ▼" : " ▲") : "")
                        }
                        Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: root.alternate }
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.alternate }
                        // Sorting only listens on the body of the cell, so a double-click on the divider
                        // never reaches it even after the fitted column changes size under the pointer.
                        Item {
                            anchors.left: parent.left; anchors.right: parent.right; anchors.rightMargin: 10; height: parent.height
                            TapHandler { acceptedButtons: Qt.LeftButton; onSingleTapped: desktop.model.sortBy(index) }
                        }
                        TapHandler { acceptedButtons: Qt.RightButton; onTapped: { columnMenu.column = index; columnMenu.popup() } }
                        Item {
                            anchors.right: parent.right; width: 10; height: parent.height
                            HoverHandler { cursorShape: Qt.SplitHCursor }
                            TapHandler { acceptedButtons: Qt.LeftButton; gesturePolicy: TapHandler.WithinBounds; onDoubleTapped: root.fitColumn(index) }
                        }
                    }
                }
                Item {
                Layout.fillWidth: true; Layout.fillHeight: true
                TableView {
                    id: table; objectName: "trackTable"; property int keyboardRow: 0
                    anchors.fill: parent
                    clip: true; model: desktop.model; reuseItems: true
                    // Title takes the remaining width so status and stores stay on screen.
                    // BPM and key only mean something for local files; SoundCloud rows never carry them.
                    // A width the user dragged or fitted wins; without one the saved width applies.
                    function chosenWidth(column) {
                        let explicit = explicitColumnWidth(column)
                        // BPM and key chips need room even when an older, narrower width was saved.
                        return Math.max(column === 4 || column === 5 ? 56 : 0, explicit >= 0 ? explicit : root.columnWidths[column])
                    }
                    columnWidthProvider: function(column) {
                        if (!root.columnVisible(column)) return 0
                        if (column !== root.titleColumn || explicitColumnWidth(column) >= 0) return chosenWidth(column)
                        let used = 0
                        for (let i = 0; i < 10; i++) if (i !== root.titleColumn && root.columnVisible(i)) used += chosenWidth(i)
                        return Math.max(150, width - used - 12)
                    }
                    property bool localView: !!desktop.view.local
                    onLocalViewChanged: forceLayout()
                    // Emitted once per column whose visual slot changed, so slots can be assigned directly.
                    onColumnMoved: (logicalIndex, oldVisualIndex, newVisualIndex) => {
                        let order = root.columnOrder.slice()
                        order[newVisualIndex] = logicalIndex
                        root.columnOrder = order
                    }
                    onWidthChanged: forceLayout()
                    resizableColumns: true
                    Connections {
                        target: desktop.model
                        function onModelReset() {
                            table.keyboardRow = 0
                            // Qt 6.11: moving a column also freezes a row mapping sized to the rows of that
                            // moment, and the next larger view aborted the app in getRowHeight. Rows are
                            // never reordered here, so the mapping is dropped; the column order stays.
                            table.clearRowReordering()
                        }
                    }
                    // Declared inside the Flickable, the handler sits on its contentItem, so the point
                    // already includes the scroll offset; mapping it again lit a row further down.
                    HoverHandler { id: tableHover }
                    readonly property int hoverRow: tableHover.hovered ? cellAtPosition(tableHover.point.position, true).y : -1
                    delegate: Rectangle {
                        id: cell
                        required property string display
                        required property bool chosen
                        required property string status
                        required property real progress
                        required property bool local
                        required property string trackKey
                        required property string camelot
                        required property int row
                        required property int column
                        readonly property bool playing: desktop.audioKey === trackKey
                        implicitHeight: 34; implicitWidth: 100
                        color: chosen ? root.selection : (row % 2 ? root.panel : root.bg)
                        // Only the hover layer fades; theme and selection colours switch at once.
                        // A delegate taken from the pool shows its new row's state without fading into it.
                        property bool recycled: false
                        TableView.onPooled: recycled = true
                        TableView.onReused: Qt.callLater(() => recycled = false)
                        Rectangle {
                            anchors.fill: parent; color: root.hoverSurface
                            opacity: !cell.chosen && cell.row === table.hoverRow ? 1 : 0
                            Behavior on opacity { enabled: root.motion && !cell.recycled; NumberAnimation { duration: 120 } }
                        }
                        readonly property string flashStatus: root.flashKeys[trackKey] || ""
                        onFlashStatusChanged: if (flashStatus) flash.restart()
                        Component.onCompleted: if (flashStatus) flash.restart()
                        ParallelAnimation {
                            id: flash
                            NumberAnimation { target: flashOverlay; property: "opacity"; from: .45; to: 0; duration: 650; easing.type: Easing.OutQuad }
                            NumberAnimation { target: cellText; property: "scale"; from: cell.column === 0 ? 1.3 : 1; to: 1; duration: 350; easing.type: Easing.OutBack }
                        }
                        Rectangle {
                            id: flashOverlay
                            anchors.fill: parent; opacity: 0
                            color: cell.flashStatus === "got" ? root.success : cell.flashStatus === "skip" ? root.muted : root.accent
                        }
                        Rectangle {
                            visible: cell.progress >= 0
                            // Each cell paints its slice of one continuous row-wide fill.
                            width: Math.max(0, Math.min(cell.width,
                                table.contentWidth * Math.max(0, Math.min(1, cell.progress)) - cell.x))
                            height: parent.height
                            color: cell.chosen ? root.fg : root.accent
                            opacity: cell.chosen ? 0.16 : root.dark ? 0.28 : 0.20
                            clip: true
                            // The sweep binding lives inside the loaded item, so idle cells never evaluate it.
                            Loader {
                                active: cell.progress >= 0 && cell.progress < 1 && root.motion
                                height: parent.height
                                sourceComponent: Rectangle {
                                    x: root.shimmerPhase * (table.contentWidth * cell.progress + 160) - 160 - cell.x
                                    width: 160; height: parent ? parent.height : 0
                                    gradient: Gradient {
                                        orientation: Gradient.Horizontal
                                        GradientStop { position: 0; color: "transparent" }
                                        GradientStop { position: .5; color: Qt.rgba(1, 1, 1, .9) }
                                        GradientStop { position: 1; color: "transparent" }
                                    }
                                }
                            }
                        }
                        Rectangle { visible: row === table.keyboardRow && table.activeFocus; anchors.fill: parent; color: "transparent"; border.color: root.accent; border.width: 1 }
                        readonly property bool chipColumn: cell.column === 4 || cell.column === 5
                        Chip {
                            visible: cell.chipColumn && cell.display !== ""
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.right: cell.column === 4 ? parent.right : undefined
                            anchors.left: cell.column === 5 ? parent.left : undefined
                            anchors.margins: 6
                            text: cell.chipColumn ? cell.display : ""
                            code: cell.column === 5 ? cell.camelot : ""
                            selected: cell.chosen
                            match: !cell.playing && (cell.column === 5 ? root.harmonic(cell.camelot, desktop.nowPlaying.camelot || "")
                                                                         : cell.column === 4 && root.tempoMatch(parseFloat(cell.display), desktop.nowPlaying.bpm || 0))
                        }
                        Label {
                            id: cellText
                            transformOrigin: Item.Left
                            visible: cell.column !== 9 && !cell.chipColumn
                            anchors.fill: parent; anchors.margins: 7; textFormat: Text.PlainText; elide: Text.ElideRight
                            horizontalAlignment: [4, 6, 8].indexOf(cell.column) >= 0 ? Text.AlignRight : Text.AlignLeft
                            font.bold: cell.column === root.titleColumn && cell.playing
                            font.features: root.tabular
                            text: (cell.column === root.titleColumn ? (cell.playing ? "▶ " : "") + (cell.local ? "▣ " : "") : "") + cell.display
                            color: cell.chosen ? root.selectionText : cell.progress >= 0 ? root.fg : cell.column === 0 ? root.statusColor(cell.status)
                                 : [3, 7, 8].indexOf(cell.column) >= 0 ? root.muted : root.fg
                        }
                        Row {
                            visible: cell.column === 9
                            anchors.fill: parent; anchors.margins: 6; spacing: 4; clip: true
                            Repeater {
                                model: cell.column === 9 ? cell.display.split(", ").filter(s => s) : []
                                Rectangle {
                                    required property string modelData
                                    width: badge.implicitWidth + 10; height: 22; radius: 4
                                    // "no-link" is the absence of a store, so it reads as quiet text rather than a badge.
                                    color: modelData === "no-link" ? "transparent" : cell.chosen ? root.panel : root.alternate
                                    Label { id: badge; anchors.centerIn: parent; text: parent.modelData; font.pixelSize: 12; color: cell.chosen && parent.modelData === "no-link" ? root.selectionText : root.storeColor(parent.modelData) }
                                }
                            }
                        }
                        MouseArea {
                            anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: function(mouse) {
                                table.forceActiveFocus()
                                table.keyboardRow = row
                                if (mouse.button !== Qt.RightButton || !chosen) desktop.model.select(row, (mouse.modifiers & Qt.ControlModifier) !== 0, (mouse.modifiers & Qt.ShiftModifier) !== 0)
                                if (mouse.button === Qt.RightButton) { contextMenu.fromToolbar = false; contextMenu.popup() }
                            }
                            onDoubleClicked: desktop.action("play")
                        }
                    }
                    function moveCursor(delta, extend) {
                        keyboardRow = Math.max(0, Math.min(rows - 1, keyboardRow + delta))
                        desktop.model.select(keyboardRow, false, extend)
                        positionViewAtRow(keyboardRow, TableView.Contain)
                    }
                    Keys.onUpPressed: event => moveCursor(-1, (event.modifiers & Qt.ShiftModifier) !== 0)
                    Keys.onDownPressed: event => moveCursor(1, (event.modifiers & Qt.ShiftModifier) !== 0)
                    Keys.onPressed: function(event) {
                        if (event.matches(StandardKey.Copy)) { desktop.copy(); event.accepted = true }
                        else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { if (root.hasSelection) desktop.action("open"); event.accepted = true }
                        else if (event.key === Qt.Key_Delete) { if (root.hasSelection && root.remoteView) desktop.action("remove"); event.accepted = true }
                        else if (event.key === Qt.Key_Home) { moveCursor(-rows, (event.modifiers & Qt.ShiftModifier) !== 0); event.accepted = true }
                        else if (event.key === Qt.Key_End) { moveCursor(rows, (event.modifiers & Qt.ShiftModifier) !== 0); event.accepted = true }
                        else if (event.key === Qt.Key_PageUp) { moveCursor(-Math.max(1, Math.floor(height / 34) - 1), (event.modifiers & Qt.ShiftModifier) !== 0); event.accepted = true }
                        else if (event.key === Qt.Key_PageDown) { moveCursor(Math.max(1, Math.floor(height / 34) - 1), (event.modifiers & Qt.ShiftModifier) !== 0); event.accepted = true }
                    }
                    ScrollBar.vertical: ScrollBar {}
                    ScrollBar.horizontal: ScrollBar {}
                    Accessible.name: qsTr("Tracks")
                }
                Label {
                    anchors.centerIn: parent; width: parent.width - 40
                    visible: !root.hasRows && desktop.ready; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: root.muted
                    text: desktop.model.counts.total > 0 ? qsTr("No tracks match the search or filters. Press Esc to clear them.")
                        : desktop.view.title ? qsTr("This view has no tracks.")
                        : qsTr("Add a playlist (A), pick one in the sidebar or add a folder (Ctrl+O) to start.")
                }
                }
                Rectangle {
                    objectName: "errorBanner"
                    visible: root.errorMessage.length > 0
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    implicitHeight: errorContent.implicitHeight + 16
                    color: root.panel; border.color: root.danger; radius: 4
                    RowLayout {
                        id: errorContent
                        anchors.fill: parent; anchors.margins: 8
                        Label {
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            text: root.errorMessage; textFormat: Text.PlainText
                            wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                            color: root.danger; Accessible.name: text
                        }
                        Button { objectName: "errorDetails"; text: qsTr("Details"); onClicked: messageLog.open() }
                        FlatButton { objectName: "dismissError"; text: qsTr("Dismiss error"); icon.source: "icons/close.svg"; icon.width: 12; icon.height: 12; onClicked: root.errorMessage = "" }
                    }
                }
            }
        }
        // Status spans the whole window below the sidebar and the table.
        Rectangle {
            objectName: "statusBar"
            Layout.fillWidth: true; Layout.minimumWidth: 0
            implicitHeight: 28; color: root.panel
            Rectangle { width: parent.width; height: 1; color: root.alternate }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 8; spacing: 8
                Label {
                    // Counts keep their width; the message beside them gives way first.
                    Layout.maximumWidth: parent.width * 0.7; Layout.minimumWidth: 0
                    color: root.muted; elide: Text.ElideRight; font.features: root.tabular
                    property var c: desktop.model.counts
                    text: root.hasRows || c.total > 0 ? qsTr("%1 / %2 tracks · owned %3 · skipped %4").arg(c.visible).arg(c.total).arg(c.got).arg(c.skipped) : ""
                }
                BusyIndicator { running: desktop.busy || root.shuttingDown || !desktop.ready; visible: running; implicitHeight: 22; implicitWidth: 22 }
                // An import shows its stage and how many of how many tracks are in.
                ProgressBar {
                    objectName: "importProgress"
                    visible: (desktop.importProgress.total || 0) > 0
                    from: 0; to: Math.max(1, desktop.importProgress.total || 0); value: desktop.importProgress.done || 0
                    Layout.preferredWidth: 160; Layout.minimumWidth: 60
                    Behavior on value { enabled: root.motion; NumberAnimation { duration: 150 } }
                }
                FlatButton { text: qsTr("Cancel"); display: AbstractButton.TextOnly; visible: desktop.busy; implicitHeight: 24; onClicked: desktop.action("cancel") }
                Label {
                    Layout.fillWidth: true; Layout.minimumWidth: 0; textFormat: Text.PlainText; elide: Text.ElideRight
                    readonly property var importing: desktop.importProgress
                    text: root.shuttingDown ? qsTr("Closing…") : !desktop.ready ? qsTr("Loading library…")
                        : importing.stage ? importing.stage + (importing.total ? " " + importing.done + " / " + importing.total : "")
                        : desktop.level === "error" ? "" : desktop.message
                    font.features: root.tabular
                    color: desktop.level === "error" ? root.danger : root.muted
                    font.bold: desktop.level === "error"
                    ToolTip.visible: hovered && text.length > 0; ToolTip.text: text
                    HoverHandler { id: messageHover }
                    property bool hovered: messageHover.hovered
                    TapHandler { onTapped: messageLog.open() }
                    Accessible.name: text
                }
            }
        }
    }
    FolderDialog { id: folderDialog; title: qsTr("Add folder"); onAccepted: desktop.addFolder(selectedFolder.toString()) }
    AppMenu {
        id: playlistMenu
        property string source
        AppMenuItem { text: qsTr("Open"); onTriggered: desktop.load(playlistMenu.source) }
        AppMenuItem { text: qsTr("Delete playlist…"); keys: "Shift+X"; onTriggered: desktop.deletePlaylist(playlistMenu.source) }
    }
    TextMetrics { id: fitMetrics }
    TextMetrics { id: badgeMetrics; font.pixelSize: 12 }
    AppMenu {
        id: columnMenu
        property int column: 0
        AppMenuItem { text: qsTr("Fit column to contents"); onTriggered: root.fitColumn(columnMenu.column) }
        AppMenuItem { text: qsTr("Fit all columns"); onTriggered: root.fitAllColumns() }
        AppMenuItem { text: qsTr("Reset column widths"); onTriggered: root.resetColumnWidths() }
        AppMenuItem { text: qsTr("Reset column order"); onTriggered: root.resetColumnOrder() }
        MenuSeparator {}
        Instantiator {
            model: 10
            delegate: AppMenuItem {
                required property int index
                objectName: "column-" + index
                // desktop.ready is notified again after every retranslation.
                text: (desktop.ready, desktop.model.headerName(index))
                checkable: true; checked: root.hiddenColumns.indexOf(index) < 0
                enabled: index !== root.titleColumn && root.columnAvailable(index)
                onTriggered: root.toggleColumn(index)
            }
            onObjectAdded: (index, object) => columnMenu.insertItem(columnMenu.count, object)
            onObjectRemoved: (index, object) => columnMenu.removeItem(object)
        }
    }
    AppMenu {
        id: contextMenu
        // From the toolbar's "More actions" button the entries already on the toolbar are left out;
        // local-file entries appear only in local views and playlist entries only in playlist views.
        property bool fromToolbar: false
        readonly property bool local: !!desktop.view.local
        AppMenuItem { text: qsTr("Play / pause"); keys: "Space"; enabled: root.loaded || root.hasSelection; onTriggered: desktop.action("play") }
        AppMenuItem { text: qsTr("Open links"); keys: "O"; visible: !contextMenu.fromToolbar; enabled: root.hasSelection; onTriggered: desktop.action("open") }
        AppMenuItem { text: qsTr("Download"); keys: "D"; visible: !contextMenu.fromToolbar; enabled: root.hasSelection && !desktop.busy; onTriggered: desktop.action("download") }
        AppSeparator { visible: !contextMenu.fromToolbar }
        AppMenuItem { text: qsTr("Mark owned"); keys: "G"; visible: !contextMenu.fromToolbar; enabled: root.hasSelection; onTriggered: desktop.mark("got") }
        AppMenuItem { text: qsTr("Skip"); keys: "K"; visible: !contextMenu.fromToolbar; enabled: root.hasSelection; onTriggered: desktop.mark("skip") }
        AppMenuItem { text: qsTr("Reset status"); keys: "U"; enabled: root.hasSelection; onTriggered: desktop.mark("new") }
        AppSeparator {}
        AppMenuItem { text: qsTr("Copy artist and title"); keys: "Ctrl+C"; enabled: root.hasSelection; onTriggered: desktop.copy() }
        AppMenuItem { text: qsTr("Analyze BPM / key"); visible: contextMenu.local && !contextMenu.fromToolbar; enabled: root.hasSelection && !desktop.busy; onTriggered: desktop.action("analyze") }
        AppMenuItem { text: qsTr("Edit BPM / key…"); keys: "E"; visible: contextMenu.local; enabled: desktop.model.counts.selected === 1; onTriggered: desktop.action("edit") }
        AppMenuItem { text: qsTr("Export audio…"); visible: contextMenu.local; enabled: root.hasSelection && !desktop.busy; onTriggered: desktop.action("export") }
        AppMenuItem { text: qsTr("Prepare cart / Beatport playlist"); keys: "C"; enabled: root.hasSelection && !desktop.busy; onTriggered: desktop.action("cart") }
        AppSeparator { visible: root.remoteView || contextMenu.local }
        AppMenuItem { text: qsTr("Remove from playlist…"); keys: "X"; visible: root.remoteView; enabled: root.hasSelection; onTriggered: desktop.action("remove") }
        AppMenuItem { text: qsTr("Delete files…"); visible: contextMenu.local; enabled: root.hasSelection && !desktop.busy; onTriggered: desktop.action("delete_files") }
    }
    FolderDialog {
        id: fieldFolder
        property string targetName
        onAccepted: {
            let updated = Object.assign({}, dialog.values)
            updated[targetName] = desktop.localPath(selectedFolder.toString())
            dialog.values = updated
        }
    }
    FileDialog {
        id: fieldFile
        property string targetName
        nameFilters: ["JSON (*.json)", "All files (*)"]
        onAccepted: {
            let updated = Object.assign({}, dialog.values)
            updated[targetName] = desktop.localPath(selectedFile.toString())
            dialog.values = updated
        }
    }
    AppDialog {
        id: helpDialog
        title: qsTr("Keyboard shortcuts")
        anchors.centerIn: parent; width: Math.min(560, root.width - 50); height: Math.min(root.height - 50, implicitHeight)
        standardButtons: Dialog.Close
        contentItem: ScrollView {
            clip: true; implicitHeight: helpText.implicitHeight + 8
            TextArea { id: helpText; readOnly: true; textFormat: Text.PlainText; font.family: "monospace"; background: null }
        }
    }
    AppDialog {
        id: messageLog
        title: qsTr("Messages")
        anchors.centerIn: parent; width: Math.min(720, root.width - 50); height: Math.min(root.height - 50, implicitHeight)
        standardButtons: Dialog.Close
        contentItem: ScrollView {
            clip: true; implicitHeight: Math.max(120, logText.implicitHeight + 8)
            TextArea { id: logText; readOnly: true; textFormat: Text.PlainText; selectByMouse: true; wrapMode: Text.Wrap; background: null
                       text: root.messages.length ? root.messages.join("\n") : qsTr("No messages yet.") }
        }
    }
    AppDialog {
        id: dialog
        property string ident: ""
        property string body: ""
        property string error: ""
        property string okText: ""
        property bool info: false
        property var fields: []
        property var values: ({})
        anchors.centerIn: parent; width: Math.min(650, root.width - 50)
        height: Math.min(root.height - 50, implicitHeight)
        standardButtons: info ? Dialog.Ok : Dialog.Ok | Dialog.Cancel
        onOkTextChanged: { let ok = standardButton(Dialog.Ok); if (ok) ok.text = okText || qsTr("OK") }
        onAccepted: desktop.answer(ident, values, true)
        onRejected: desktop.answer(ident, {}, false)
        onClosed: Qt.callLater(root.nextQuestion)
        onOpened: {
            let ok = standardButton(Dialog.Ok)
            if (ok) { ok.text = okText || qsTr("OK"); ok.highlighted = true; if (fields.length === 0) ok.forceActiveFocus() }
        }
        contentItem: ScrollView {
            id: dialogScroll
            clip: true
            // Enter submits unless a multi-line editor has focus.
            Keys.onReturnPressed: event => { if (root.activeFocusItem && "textDocument" in root.activeFocusItem) event.accepted = false; else dialog.accept() }
            Keys.onEnterPressed: event => { if (root.activeFocusItem && "textDocument" in root.activeFocusItem) event.accepted = false; else dialog.accept() }
            implicitHeight: Math.min(root.height - 160, dialogColumn.implicitHeight + 8)
            ColumnLayout {
                id: dialogColumn
                width: dialog.availableWidth - 8
                spacing: 8
                Label {
                    Layout.fillWidth: true; visible: dialog.error.length > 0
                    text: dialog.error; textFormat: Text.PlainText; wrapMode: Text.WordWrap; color: root.danger; font.bold: true
                    Accessible.name: text
                }
                Label { Layout.fillWidth: true; text: dialog.body; textFormat: Text.PlainText; wrapMode: Text.Wrap; visible: text.length > 0 }
                Repeater {
                    model: dialog.fields
                    ColumnLayout {
                        id: fieldItem
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true; spacing: 2
                        Label { text: modelData.label; textFormat: Text.PlainText; color: root.muted; visible: text.length > 0 && modelData.kind !== "bool" }
                        RowLayout {
                            Layout.fillWidth: true
                            visible: ["text", "folder", "file", "savefile", "number"].indexOf(fieldItem.modelData.kind) >= 0
                            TextField {
                                id: fieldInput
                                Layout.fillWidth: true
                                text: String(dialog.values[fieldItem.modelData.name] ?? fieldItem.modelData.value)
                                onTextEdited: dialog.values[fieldItem.modelData.name] = text
                                validator: fieldItem.modelData.kind === "number" ? numberValidator : null
                                inputMethodHints: fieldItem.modelData.kind === "number" ? Qt.ImhFormattedNumbersOnly : Qt.ImhNone
                                Accessible.name: fieldItem.modelData.label
                                onAccepted: dialog.accept()
                                Component.onCompleted: if (fieldItem.index === 0 && parent.visible) forceActiveFocus()
                            }
                            Button {
                                text: qsTr("Browse…")
                                visible: ["folder", "file", "savefile"].indexOf(fieldItem.modelData.kind) >= 0
                                onClicked: {
                                    if (fieldItem.modelData.kind === "folder") {
                                        fieldFolder.targetName = fieldItem.modelData.name
                                        fieldFolder.open()
                                    } else {
                                        fieldFile.targetName = fieldItem.modelData.name
                                        fieldFile.fileMode = fieldItem.modelData.kind === "savefile" ? FileDialog.SaveFile : FileDialog.OpenFile
                                        fieldFile.open()
                                    }
                                }
                            }
                        }
                        RowLayout {
                            visible: fieldItem.modelData.name === "bpm"
                            Button { text: "÷2"; onClicked: dialog.values = Object.assign({}, dialog.values, {bpm: String(Number(dialog.values.bpm) / 2)}) }
                            Button { text: "×2"; onClicked: dialog.values = Object.assign({}, dialog.values, {bpm: String(Number(dialog.values.bpm) * 2)}) }
                            Button { text: qsTr("Clear manual values"); onClicked: dialog.values = Object.assign({}, dialog.values, {bpm: "", key: ""}) }
                        }
                        ComboBox {
                            visible: fieldItem.modelData.kind === "choice"; Layout.fillWidth: true
                            model: fieldItem.modelData.options.map(o => o[1])
                            currentIndex: Math.max(0, fieldItem.modelData.options.findIndex(o => String(o[0]) === String(dialog.values[fieldItem.modelData.name] ?? fieldItem.modelData.value)))
                            onActivated: dialog.values[fieldItem.modelData.name] = fieldItem.modelData.options[currentIndex][0]
                            Accessible.name: fieldItem.modelData.label
                        }
                        Frame {
                            visible: fieldItem.modelData.kind === "multiline" || fieldItem.modelData.kind === "log"; Layout.fillWidth: true
                            padding: 1
                            background: Rectangle { color: root.panel; border.color: root.alternate; radius: 3 }
                            ScrollView {
                                anchors.fill: parent
                                implicitHeight: fieldItem.modelData.kind === "log" ? Math.min(360, Math.max(120, area.contentHeight + 16)) : Math.min(160, Math.max(72, area.contentHeight + 16))
                                TextArea {
                                    id: area
                                    text: String(fieldItem.modelData.value); textFormat: Text.PlainText
                                    readOnly: fieldItem.modelData.kind === "log"; selectByMouse: true
                                    wrapMode: fieldItem.modelData.kind === "log" ? Text.NoWrap : Text.Wrap
                                    font.family: fieldItem.modelData.kind === "log" ? "monospace" : Qt.application.font.family
                                    onTextChanged: if (!readOnly) dialog.values[fieldItem.modelData.name] = text
                                    Accessible.name: fieldItem.modelData.label
                                }
                            }
                        }
                        // Several ticks under one label, in two columns where the dialog is wide enough.
                        GridLayout {
                            visible: fieldItem.modelData.kind === "checks"; Layout.fillWidth: true
                            columns: dialog.availableWidth >= 520 ? 2 : 1; columnSpacing: 12; rowSpacing: 0
                            Repeater {
                                model: fieldItem.modelData.kind === "checks" ? fieldItem.modelData.options : []
                                CheckBox {
                                    id: option
                                    required property var modelData
                                    objectName: "check-" + fieldItem.modelData.name + "-" + modelData[0]
                                    Layout.fillWidth: true; Layout.preferredWidth: 1
                                    text: modelData[1]; Accessible.name: text
                                    checked: (dialog.values[fieldItem.modelData.name] ?? fieldItem.modelData.value).indexOf(modelData[0]) >= 0
                                    onToggled: {
                                        let chosen = (dialog.values[fieldItem.modelData.name] ?? fieldItem.modelData.value).filter(v => v !== modelData[0])
                                        if (checked) chosen.push(modelData[0])
                                        dialog.values[fieldItem.modelData.name] = chosen
                                    }
                                    contentItem: Label {
                                        leftPadding: option.indicator.width + option.spacing
                                        text: option.text; textFormat: Text.PlainText; wrapMode: Text.Wrap
                                        verticalAlignment: Text.AlignVCenter; color: root.fg
                                    }
                                }
                            }
                        }
                        CheckBox {
                            visible: fieldItem.modelData.kind === "bool"; text: fieldItem.modelData.label
                            checked: (dialog.values[fieldItem.modelData.name] ?? fieldItem.modelData.value) === true
                            onToggled: dialog.values[fieldItem.modelData.name] = checked; Accessible.name: fieldItem.modelData.label
                        }
                    }
                }
            }
        }
    }
    DoubleValidator { id: numberValidator; bottom: 0; top: 999; notation: DoubleValidator.StandardNotation; locale: "C" }
}
