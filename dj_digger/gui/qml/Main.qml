import QtQuick
import QtQuick.Controls
import QtQuick.Controls.impl
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Effects

ApplicationWindow {
    id: root
    width: 1200; height: 800
    minimumWidth: 760; minimumHeight: 520
    visible: true
    title: "dj-digger" + (desktop.view.title ? " — " + desktop.view.title : "")
    property string languageCode: systemLanguage
    property string themeChoice: "system"
    // High contrast is the dark theme with black surfaces, white text and brighter outlines.
    readonly property bool highContrast: themeChoice === "contrast"
    property bool dark: highContrast || themeChoice === "dark" || (themeChoice === "system" && Application.styleHints.colorScheme === Qt.Dark)
    // Club palette: near-black surfaces, electric blue and bordeaux accents, metallic silver text.
    property color bg: highContrast ? "#000000" : dark ? "#0b0b0e" : "#eceef1"
    property color panel: highContrast ? "#0d0d0f" : dark ? "#141418" : "#fafafb"
    property color fg: highContrast ? "#ffffff" : dark ? "#ececef" : "#111217"
    property color muted: highContrast ? "#e2e3e6" : dark ? "#a3a6ad" : "#50535c"
    property color alternate: dark ? "#1e1e24" : "#dfe2e7"
    property color hoverSurface: dark ? "#26262e" : "#d6dae1"
    property color pressedSurface: dark ? "#35353f" : "#c1c6cf"
    // Clearly dimmer than muted: disabled controls must not read as merely secondary.
    property color disabledFg: highContrast ? "#8b8e96" : dark ? "#5f626b" : "#9a9da5"
    // Selection is a soft, low-chroma blue: a whole list selected must not glare.
    property color selection: dark ? "#34466f" : "#b8c9f0"
    property color selectionText: dark ? "#ffffff" : "#111217"
    property color accent: dark ? "#2f63ea" : "#1f4fd1"
    property color accent2: dark ? "#c02a4e" : "#8c1a36"
    // Secondary text (the artist): 4.5:1 against the panel in both themes.
    property color silver: highContrast ? "#e2e3e6" : dark ? "#c9ccd3" : "#666a73"
    property color success: dark ? "#6fd08c" : "#1c7a3c"
    property color warning: dark ? "#e3b85c" : "#8a5a00"
    property color danger: dark ? "#ff7088" : "#a3122f"
    // Field outlines, checkbox frames and scroll handles: 3:1 against both surfaces.
    property color border: highContrast ? "#a0a3ab" : dark ? "#6a6d76" : "#7d818b"
    // One type scale from the system font, so its size and DPI scaling apply everywhere; at the
    // 14 px desktop default these are the sizes the window was drawn with.
    readonly property int fontBody: Qt.application.font.pixelSize > 0 ? Qt.application.font.pixelSize : Math.round(Qt.application.font.pointSize * 4 / 3)
    readonly property int fontSmall: Math.round(fontBody * .8)
    readonly property int fontCaption: Math.round(fontBody * .86)
    readonly property int fontLarge: Math.round(fontBody * 1.07)
    readonly property int fontTitle: Math.round(fontBody * 1.2)
    readonly property int fontDisplay: Math.round(fontBody * 1.43)
    // Icon buttons come in two sizes: 16 px in toolbars, lists and banners, 20 px in the transport.
    readonly property int iconSmall: 16
    readonly property int iconLarge: 20
    // Hover tooltips wait a moment, so passing the pointer over controls does not flash them.
    readonly property int tipDelay: 500
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
    // Normal colours remain visible at rest; only kicks light them up.
    readonly property bool live: desktop.playing
    // The light follows the kick level the player computes ahead of the speaker,
    // sampled at the heard time: it jumps up with a kick at once and dies away like
    // a lamp, quickly at first and then more slowly (time constant 75 ms), under the
    // gate within 150 ms, so every kick shows in proportion, none is missed, and the
    // light goes out between kicks even at 174 BPM. Below 15 % nothing shows: even a
    // faint neon tint reads as light.
    readonly property real decay: 75
    readonly property real gate: .15
    // A frame reaches the eye two or three frames after it is computed (scene graph,
    // compositor, monitor), so the light samples the level that much ahead.
    readonly property real displayLead: .04
    property real flash: 0
    readonly property real glow: Math.max(0, (flash - gate) / (1 - gate))
    // Levels are copied once per change; the frame loop reads these, not the Python property.
    property string kickKey: ""
    property real kickStart: 0
    property real kickStep: .01
    property var kickLevels: []
    Connections {
        target: desktop
        function onKicksChanged() {
            root.kickKey = desktop.kicks.key || ""
            root.kickStart = desktop.kicks.start || 0
            root.kickStep = desktop.kicks.step || .01
            root.kickLevels = desktop.kicks.levels || []
            root.anchorBeat()
        }
    }
    // Where the zoomed waveform's beat ticks line up: a strong kick, kept while the next ones fall on
    // the grid of the analysed BPM; -1 without a BPM or a kick.
    property real beatAnchor: -1
    function anchorBeat() {
        let bpm = desktop.nowPlaying.bpm || 0, levels = kickLevels, best = .6, at = -1
        if (bpm <= 0 || kickKey !== desktop.audioKey) { beatAnchor = -1; return }
        for (let i = 0; i < levels.length; i++) if (levels[i] > best) { best = levels[i]; at = i }
        if (at < 0) return
        let time = kickStart + at * kickStep, period = 60 / bpm
        let off = beatAnchor < 0 ? period / 2 : ((time - beatAnchor) % period + period) % period
        if (Math.min(off, period - off) > .04) beatAnchor = time
    }
    readonly property bool pulsing: live && motion
    // The track time reaching the speaker at clockAt, from the player's own
    // measurement of what it has queued; -1 until the first snapshot.
    property real audioClock: -1
    property real clockAt: 0
    property real lastPosition: 0
    property string clockKey: ""
    // Seeks the backend has served; a change is a real jump, a position step alone may be lag.
    property int clockSeeks: 0
    property real lastTick: 0
    // Heard audio time, interpolated every frame while pulsing; -1 falls back to the snapshot position.
    property real playhead: -1
    onPulsingChanged: if (!pulsing) { flash = 0; audioClock = -1; playhead = -1 }
    Connections {
        target: desktop
        function onAudioChanged() {
            let audio = desktop.audio
            if (root.pulsing) root.syncClock(audio.heard === undefined ? audio.position : audio.heard, audio.at ? audio.at * 1000 : Date.now(), audio.position)
        }
    }
    FrameAnimation {
        running: root.pulsing && root.visibility !== Window.Minimized
        onTriggered: root.pulseTick(Date.now())
    }
    // ``heard`` was the speaker's track time at ``at`` (ms since the epoch).
    function syncClock(heard, at, position, now) {
        now = now === undefined ? Date.now() : now
        heard += (now - at) / 1000
        let predicted = audioClock + (now - clockAt) / 1000
        let key = desktop.audioKey, seeks = desktop.audio.seeks || 0
        // Another track or a seek the backend served starts over; small differences ease in,
        // and a late snapshot after an audio hiccup only re-anchors the clock.
        let jumped = key !== clockKey || seeks !== clockSeeks
        clockSeeks = seeks
        if (audioClock < 0 || jumped || Math.abs(heard - predicted) > .15) {
            audioClock = heard
            if (jumped) flash = 0
        } else {
            audioClock = predicted + .25 * (heard - predicted)
        }
        playhead = Math.max(0, Math.min(position, audioClock))
        clockKey = key
        lastPosition = position
        clockAt = now
    }
    // The kick level at a track time, between the 10 ms levels; 0 outside them.
    function kickLevel(time) {
        if (kickKey !== clockKey) return 0
        let x = (time - kickStart) / kickStep, i = Math.floor(x), levels = kickLevels
        if (i < 0 || i >= levels.length) return 0
        return i + 1 < levels.length ? levels[i] + (levels[i + 1] - levels[i]) * (x - i) : levels[i]
    }
    function pulseTick(now) {
        if (!pulsing || audioClock < 0) return
        let heard = Math.min(lastPosition, audioClock + (now - clockAt) / 1000)
        playhead = Math.max(0, heard)
        let elapsed = Math.max(0, now - lastTick)
        lastTick = now
        flash = Math.max(kickLevel(heard + displayLead), flash * Math.exp(-elapsed / decay))
        if (flash < .005) flash = 0
    }
    // One shared sweep for every download fill, running only while something is busy.
    property real shimmerPhase: 0
    NumberAnimation on shimmerPhase {
        from: 0; to: 1; duration: 1400; loops: Animation.Infinite
        running: root.motion && desktop.busy && root.visibility !== Window.Minimized
    }
    // One right margin for the player, search, toolbar and table, so their edges line up.
    readonly property int edge: 8
    property real sidebarWidth: 220
    property bool sidebarVisible: true
    // Where F11 returns to: windowed or maximized.
    property int windowedVisibility: Window.Windowed
    // Last opened playlists and folders, newest first; playlists gone from the sidebar drop out.
    property var recent: []
    readonly property var recentEntries: recent.map(r => ({kind: r.kind, target: r.target,
        title: r.kind === "folder" ? r.target.split(/[\\/]/).filter(Boolean).pop() || r.target
                                   : (desktop.playlists.find(p => p.source === r.target) || {}).title}))
        .filter(r => r.title)
    readonly property string recentKey: folderView ? "folder\n" + desktop.folder.path : desktop.view.source ? "playlist\n" + desktop.view.source : ""
    onRecentKeyChanged: if (recentKey) {
        let [kind, target] = recentKey.split("\n")
        recent = [{kind: kind, target: target}].concat(recent.filter(r => r.kind !== kind || r.target !== target)).slice(0, 8)
    }
    // Sidebar sections folded away from their headers ("playlists", "folders"); saved in gui.json.
    property var sidebarCollapsed: []
    function sectionOpen(section) { return sidebarCollapsed.indexOf(section) < 0 }
    function toggleSection(section) {
        sidebarCollapsed = sectionOpen(section) ? sidebarCollapsed.concat([section]) : sidebarCollapsed.filter(s => s !== section)
    }
    // The sidebar filter, lower case; playlists and folders whose names hold it stay listed.
    readonly property string sidebarQuery: sidebarFilter.text.trim().toLowerCase()
    function sidebarMatch(name) { return !sidebarQuery || String(name).toLowerCase().indexOf(sidebarQuery) >= 0 }
    // Keyboard moves inside the sidebar keep the focused row in view.
    function revealInSidebar(item) {
        let flick = sidebarScroll.contentItem
        let y = item.mapToItem(flick.contentItem, 0, 0).y
        if (y < flick.contentY) flick.contentY = y
        else if (y + item.height > flick.contentY + flick.height) flick.contentY = y + item.height - flick.height
    }
    // What selected tracks dragged from the table carry; only local playlists accept it.
    readonly property string trackDragKey: "application/x-dj-digger-tracks"
    property bool shuttingDown: false
    readonly property bool isMuted: desktop.muted
    property real volume: desktop.volume
    // The clock shows the time left instead of the time played; a click on it switches.
    property bool remainingTime: false
    // Where a newly started track begins: "" at its start, "drop" at the estimated drop, "30" at 30 %.
    property string previewStart: ""
    // Bars coloured by their low/mid/high mix (warm lows, cool highs) instead of the colour pair.
    property bool spectral: true
    // The artwork on the record's label; off, the plain square picture fills the cover.
    property bool vinylArtwork: true
    // Hot cues per track key for this session: four track times, or null where none is set.
    // ponytail: kept in memory only; the library has no place for them yet.
    property var cues: ({})
    readonly property var trackCues: cues[desktop.audioKey] || [null, null, null, null]
    readonly property real dropTime: desktop.waveformKey === desktop.audioKey && desktop.waveformDrop >= 0
                                     ? desktop.waveformDrop * (desktop.audio.duration || 0) : -1
    property var pendingQuestions: []
    property var messages: []
    property string errorMessage: ""
    // Space and the Play button only pause or resume the loaded track; Enter starts the selected one.
    readonly property bool pauseTarget: desktop.playing
    // Format (10) and Comment (11) came later: appended, so saved widths, order and hidden columns keep their meaning.
    readonly property var allColumns: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    readonly property var defaultColumnWidths: [110, 150, 280, 85, 64, 64, 58, 95, 70, 140, 110, 180]
    // Statuses, numbers and times are never cut short, whatever width was saved or dragged.
    readonly property var minimumColumnWidths: [104, 0, 0, 0, 56, 56, 58, 0, 70, 0, 0, 0]
    property var columnWidths: defaultColumnWidths.slice()
    property var hiddenColumns: [10, 11]
    // Logical column per visual position; TableView keeps delegates and widths logical.
    property var columnOrder: allColumns.slice()
    readonly property int titleColumn: 2
    property bool compactRows: false
    readonly property int rowHeight: compactRows ? 26 : 34
    function applyColumnOrder(order) {
        for (let visual = 0; visual < order.length; visual++) table.moveColumn(order[visual], visual)
    }
    function resetColumnOrder() {
        table.clearColumnReordering()
        columnOrder = allColumns.slice()
    }
    function resetLayout() {
        resetColumnWidths(); resetColumnOrder()
        sidebarWidth = 220; sidebar.SplitView.preferredWidth = sidebarWidth
    }
    function localizeButtons(target) {
        let cancel = target.standardButton(Dialog.Cancel); if (cancel) cancel.text = qsTr("Cancel")
        let close = target.standardButton(Dialog.Close); if (close) close.text = qsTr("Close")
    }
    // BPM, key, format and comment come from local files; genre, year and label from SoundCloud or a file's tags.
    function columnAvailable(column) { return (column !== 4 && column !== 5 && column < 10) || !!desktop.view.local }
    // The outermost visible columns, in on-screen order, close the keyboard row's outline.
    readonly property int firstVisibleColumn: columnOrder.find(c => columnVisible(c)) ?? 0
    readonly property int lastVisibleColumn: columnOrder.slice().reverse().find(c => columnVisible(c)) ?? 0
    function columnVisible(column) { return columnAvailable(column) && hiddenColumns.indexOf(column) < 0 }
    // Width of the widest visible value (or the header) in a column, in cell pixels.
    function contentWidth(column) {
        let sample = desktop.model.longestText(column)
        let needed = 0
        if (column === 9) {
            needed = 12
            for (let part of sample.split(", ").filter(s => s && s !== "no-link")) { badgeMetrics.text = part; needed += badgeMetrics.width + 14 }
        } else {
            fitMetrics.text = sample
            // The title keeps an 18 px play-state slot and room for the found-locally folder.
            needed = fitMetrics.width + 14 + (column === titleColumn ? 44 : column === 4 || column === 5 ? 14 : 0)
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
    function fitAllColumns() { for (let i of allColumns) fitColumn(i) }
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
    palette.mid: border
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

    // m:ss, or h:mm:ss from an hour: a two-hour mix reads 2:20:08, not 140:08.
    function clock(seconds) {
        let s = Math.max(0, Math.floor(seconds || 0)), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60)
        let two = n => (n < 10 ? "0" : "") + n
        return (h ? h + ":" + two(m) : m) + ":" + two(s % 60)
    }
    // Every store badge looks the same; only a download gate, which asks something of you, stands out.
    function storeColor(name) { return name === "gate" || name === "↓gate" ? warning : fg }
    // Keys go around the Camelot wheel in twelve hues a step of 30° apart in OKLCH, so no two
    // neighbours look alike (HSL bunched four keys into green); B (major) is a lighter shade than A.
    readonly property var keyHues: ["#00bca2", "#00b6d1", "#26a9f1", "#7a97fb", "#ad87ed", "#d179ca",
                                    "#e7729b", "#ed7665", "#e18528", "#c39900", "#93ab2c", "#4cb86a"]
    function keyTint(code, alpha) {
        if (!code) return Qt.rgba(0, 0, 0, 0)
        let hue = Qt.color(keyHues[((parseInt(code) || 1) - 1) % 12])
        if (code.endsWith("B")) hue = Qt.lighter(hue, 1.2)
        return Qt.rgba(hue.r, hue.g, hue.b, alpha)
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
        return status === "got" ? success : status === "skip" || status === "played" ? muted : status === "opened" ? warning : fg
    }
    function note(text, level) {
        messages = [(new Date()).toLocaleTimeString(Qt.locale(), Locale.ShortFormat) + (level === "error" ? " ! " : "   ") + text].concat(messages).slice(0, 50)
    }
    // Pause or resume the loaded track; with nothing loaded (at startup, after Stop) start the selected one.
    function playPause() {
        if (loaded) desktop.transport("toggle", 0)
        else if (hasSelection) desktop.action("play")
    }
    function nudge(seconds) { if (loaded) desktop.transport("nudge", seconds) }
    function seekTo(seconds) {
        // The key, not ``loaded``: a new track's handlers run before the bindings on its snapshot.
        if (!desktop.audioKey) return
        let duration = desktop.audio.duration || 0
        waveform.pending = duration > 0 ? Math.max(0, Math.min(1, seconds / duration)) : -1; pendingTimer.restart()
        desktop.transport("seek", Math.max(0, seconds))
    }
    function jumpTo(fraction) { seekTo(fraction * (desktop.audio.duration || 0)) }
    function setCue(index) {
        if (!loaded) return
        let all = Object.assign({}, cues), list = trackCues.slice()
        list[index] = waveform.heard
        all[desktop.audioKey] = list
        cues = all
    }
    function jumpCue(index) { if (typeof trackCues[index] === "number") seekTo(trackCues[index]) }
    function jumpToDrop() { if (dropTime >= 0) seekTo(dropTime) }
    // A newly started track begins where View → Start previews says, once, unless the listener seeks first.
    property string previewKey: ""
    property int previewSeeks: 0
    property bool previewDone: false
    function applyPreviewStart() {
        let key = desktop.audioKey, duration = desktop.audio.duration || 0
        if (key !== previewKey) { previewKey = key; previewSeeks = desktop.audio.seeks || 0; previewDone = !key }
        if (previewDone || !previewStart || duration <= 0) return
        if ((desktop.audio.seeks || 0) !== previewSeeks) { previewDone = true; return }
        // The drop needs the waveform; a track without one starts at 30 %.
        if (previewStart === "drop" && desktop.waveformKey !== key) return
        previewDone = true
        seekTo(previewStart === "drop" && desktop.waveformDrop >= 0 ? desktop.waveformDrop * duration : .3 * duration)
    }
    Connections {
        target: desktop
        function onPlayingChanged() { root.applyPreviewStart() }
        function onWaveformChanged() { root.applyPreviewStart() }
    }
    function changeVolume(delta) {
        volume = Math.max(0, Math.min(1, volume + delta))
        desktop.transport("volume", volume)
    }
    // The backend owns mute; the button shows what it reports.
    function toggleMute() { desktop.transport("mute", 0) }
    // A dragged slider sends at most one level every 50 ms, the latest.
    Timer { id: volumeSend; interval: 50; onTriggered: desktop.transport("volume", root.volume) }
    function clearFilters() {
        if (hasSelection) desktop.model.clearSelection()
        else if (search.text) search.text = ""
        else resetFilters(true)
        table.forceActiveFocus()
    }
    // Filters live here and reach the model through applyFilters; the toolbar only shows them.
    property var storeFilter: []
    property string statusFilter: ""
    property int bpmMin: 0
    property int bpmMax: 0
    property bool keyMatch: false
    property string genreFilter: ""
    readonly property var statusSets: ({ unhandled: ["new", "played", "opened"], new: ["new", "played"], opened: ["opened"], got: ["got"], skip: ["skip"] })
    readonly property bool extraFilters: bpmMin > 0 || bpmMax > 0 || keyMatch || genreFilter !== ""
    readonly property bool filtersActive: search.text !== "" || storeFilter.length > 0 || statusFilter !== "" || extraFilters
    function applyFilters() {
        let playing = desktop.nowPlaying.camelot || "", codes = []
        if (keyMatch && playing)
            for (let n = 1; n <= 12; n++) for (let mode of ["A", "B"]) if (harmonic(n + mode, playing)) codes.push(n + mode)
        desktop.model.filter(search.text, { stores: storeFilter, statuses: statusSets[statusFilter] || [], bpmMin: bpmMin, bpmMax: bpmMax, keys: codes, genre: genreFilter })
    }
    function setStatusFilter(value) { statusFilter = value; filterTimer.restart() }
    function toggleStore(name) {
        let chosen = storeFilter.slice(), at = chosen.indexOf(name)
        if (at >= 0) chosen.splice(at, 1); else chosen.push(name)
        storeFilter = chosen
        filterTimer.restart()
    }
    // Esc and "Clear filters" clear everything; another view keeps only the status filter, a way of working.
    function resetFilters(everything) {
        search.text = ""
        storeFilter = []; bpmMin = 0; bpmMax = 0; keyMatch = false; genreFilter = ""
        if (everything) statusFilter = ""
        filterTimer.stop()
        applyFilters()
    }
    readonly property string viewIdentity: desktop.view.source || desktop.view.title || ""
    // Another view starts its store pills from the first one.
    onViewIdentityChanged: { resetFilters(false); storePills.contentX = 0 }
    Connections {
        target: desktop.model
        // A store that left the view leaves the filter too; counts changing never shift it.
        function onStoresChanged() {
            let present = desktop.model.stores.map(s => s.name)
            let kept = root.storeFilter.filter(name => present.indexOf(name) >= 0)
            if (kept.length !== root.storeFilter.length) { root.storeFilter = kept; filterTimer.restart() }
        }
    }
    Connections {
        target: desktop
        function onNowPlayingChanged() { if (root.keyMatch) filterTimer.restart() }
    }
    function shortcutList() {
        let lines = []
        let line = (keys, text) => lines.push("  " + keys + "\t" + text)
        // Entries of a menu and its submenus; separators and section titles have no shortcutText.
        let entries = menu => {
            let found = []
            for (let i = 0; i < menu.count; i++) {
                let sub = menu.menuAt(i), item = menu.itemAt(i)
                if (sub) found = found.concat(entries(sub))
                else if (item && item.shortcutText) found.push(item)
            }
            return found
        }
        for (let m = 0; m < menuBar.count; m++) {
            let menu = menuBar.menuAt(m)
            lines.push("\n" + menu.title)
            for (let item of entries(menu)) line(item.shortcutText, item.text)
        }
        // Actions the toolbar and the sidebar already show keep their keys here.
        let section = (title, actions) => {
            lines.push("\n" + title)
            for (let action of actions) if (action && action.shortcut) line(action.shortcut, action.text)
        }
        section(qsTr("Selected tracks"), [openAction, downloadAction, ownedAction, skipAction, resetAction, editAction, cartAction, removeAction])
        section(qsTr("Other"), [addPlaylistAction, addFolderAction, deletePlaylistAction, searchAction, hideAction])
        lines.push("\n" + qsTr("Track list"))
        lines.push("  Enter\t" + qsTr("Play the selected track"), "  Delete\t" + qsTr("Remove from playlist"),
                   "  Esc\t" + qsTr("Clear selection, then search, then filters"), "  Ctrl+C\t" + qsTr("Copy artist and title"),
                   "  Shift+↑/↓\t" + qsTr("Extend selection"), "  Ctrl+Shift+↑/↓\t" + qsTr("Add a range to the selection"),
                   "  " + qsTr("Drag across rows") + "\t" + qsTr("Select a range"), "  " + qsTr("Double click") + "\t" + qsTr("Play"),
                   "  F2\t" + qsTr("Rename file (folder views)"),
                   "  " + qsTr("Click a selected name again") + "\t" + qsTr("Edit artist or track name (folder views)"))
        lines.push("\n" + qsTr("Waveform"))
        lines.push("  1–9, 0\t" + qsTr("Jump to 10–90 % of the track, 0 to its start"),
                   "  Ctrl+1–4\t" + qsTr("Set hot cue 1–4 at the playhead"), "  Alt+1–4\t" + qsTr("Jump to hot cue 1–4"),
                   "  " + qsTr("Mouse wheel over the waveform") + "\t" + qsTr("Seek 5 s"))
        return lines.join("\n")
    }

    Component.onCompleted: {
        let s = desktop.settings()
        width = Math.min(Screen.width, Math.max(760, Number(s.width) || 1200))
        height = Math.min(Screen.height, Math.max(520, Number(s.height) || 800))
        languageCode = ["en", "de", "es", "fr", "it", "pl", "ru"].indexOf(s.language) >= 0 ? s.language : systemLanguage
        themeChoice = ["system", "light", "dark", "contrast"].indexOf(s.theme) >= 0 ? s.theme : "system"
        // Lists arrive as Qt sequences, which Array.isArray rejects; copied, they restore at last.
        let list = v => v && typeof v === "object" && typeof v.length === "number" ? Array.from(v) : null
        let widths = list(s.columnWidths), hidden = list(s.hiddenColumns), saved = list(s.columnOrder)
        // Settings saved with ten columns get Format and Comment appended, hidden.
        let legacy = !!saved && saved.length === 10
        if (widths && (widths.length === 10 || widths.length === allColumns.length))
            columnWidths = widths.concat(defaultColumnWidths.slice(widths.length)).map(v => Math.max(40, Math.min(600, Number(v) || 100)))
        if (hidden)
            hiddenColumns = hidden.map(Number).filter(v => Number.isInteger(v) && v >= 0 && v < allColumns.length && v !== titleColumn)
                .concat(legacy ? [10, 11] : [])
        let order = saved ? saved.map(Number).concat(legacy ? [10, 11] : []) : []
        if (order.length === allColumns.length && allColumns.every(i => order.indexOf(i) >= 0))
            applyColumnOrder(order)
        if (Number.isInteger(s.sortColumn)) desktop.model.setSort(s.sortColumn, s.sortReverse === true)
        if (s.compactRows === true) compactRows = true
        sidebarWidth = Math.max(160, Math.min(400, Number(s.sidebarWidth) || 220))
        if (s.sidebarVisible === false) sidebarVisible = false
        if (Array.isArray(s.sidebarCollapsed)) sidebarCollapsed = s.sidebarCollapsed.filter(v => v === "playlists" || v === "folders")
        if (s.animations === false) animations = false
        keyNotation = s.keyNotation === "classic" ? "classic" : "camelot"
        if (typeof s.colors === "string" && colorPairs.hasOwnProperty(s.colors)) colorPair = s.colors
        if (list(s.recent))
            recent = list(s.recent).filter(r => r && (r.kind === "folder" || r.kind === "playlist") && typeof r.target === "string" && r.target)
                             .slice(0, 8).map(r => ({kind: r.kind, target: r.target}))
        remainingTime = s.remainingTime === true
        previewStart = s.previewStart === "drop" || s.previewStart === "30" ? s.previewStart : ""
        spectral = s.spectral !== false
        vinylArtwork = s.vinyl !== false
        desktop.browserLook(pairColors, dark)
        desktop.language(languageCode)
        table.forceActiveFocus()
    }
    onClosing: function(event) {
        event.accepted = false
        if (!shuttingDown) {
            shuttingDown = true
            for (let i of allColumns) if (i !== titleColumn && columnVisible(i)) columnWidths[i] = table.columnWidth(i)
            desktop.saveSettings({sidebarWidth: sidebar.width, sidebarVisible: sidebarVisible, sidebarCollapsed: sidebarCollapsed, width: width, height: height, language: languageCode,
                                  theme: themeChoice, columnWidths: columnWidths, hiddenColumns: hiddenColumns, columnOrder: columnOrder,
                                  keyNotation: keyNotation, animations: animations, colors: colorPair, recent: recent,
                                  sortColumn: desktop.model.sortColumn, sortReverse: desktop.model.sortReverse, compactRows: compactRows,
                                  remainingTime: remainingTime, previewStart: previewStart, spectral: spectral, vinyl: vinylArtwork})
            desktop.close()
        }
    }
    function nextQuestion() {
        if (dialog.visible || pendingQuestions.length === 0) return
        let q = pendingQuestions.shift()
        dialog.ident = q.id; dialog.title = q.title; dialog.body = q.body; dialog.error = q.error || ""
        // Read-only rows and section headers are not answers; a folded section starts folded.
        let values = {}, collapsed = {}
        for (let f of q.fields) {
            if (["note", "path", "copy", "section"].indexOf(f.kind) < 0) values[f.name] = f.value
            if (f.kind === "section" && f.collapsed === true) collapsed[f.name] = true
        }
        dialog.values = values; dialog.collapsed = collapsed; dialog.fields = q.fields
        dialog.okText = q.ok || ""; dialog.cancelText = q.cancel || ""; dialog.info = !!q.info
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
        // Errors stay in the banner until dismissed; everything else is confirmed in a toast.
        function onNotice(text, level) {
            root.note(text, level)
            if (level === "error") root.errorMessage = text
            else root.toast(text)
        }
    }

    // Actions shown by the toolbar, the player or the sidebar live here, outside the menus,
    // so their shortcuts work everywhere without a second copy in the menu bar.
    // Store links, downloads, owning and carts belong to playlists; local files have none of them.
    // Opening runs as an operation of its own, so it waits for the running one like Download.
    Action { id: openAction; text: qsTr("Open in browser"); shortcut: "O"; enabled: root.hasSelection && !desktop.busy && !desktop.view.local; onTriggered: desktop.action("open") }
    Action { id: downloadAction; text: qsTr("Download"); shortcut: "D"; enabled: root.hasSelection && !desktop.busy && !desktop.view.local; onTriggered: desktop.action("download") }
    Action { id: ownedAction; text: qsTr("Mark owned"); shortcut: "G"; enabled: root.hasSelection && !desktop.view.local; onTriggered: desktop.mark("got") }
    Action { id: skipAction; text: qsTr("Skip"); shortcut: "K"; enabled: root.hasSelection; onTriggered: desktop.mark("skip") }
    Action { id: resetAction; text: qsTr("Reset status"); shortcut: "U"; enabled: root.hasSelection; onTriggered: desktop.mark("new") }
    Action { id: analyzeAction; text: qsTr("Analyze BPM / key"); enabled: root.hasSelection && !desktop.busy && !!desktop.view.local; onTriggered: desktop.action("analyze") }
    Action { id: editAction; text: qsTr("Edit BPM / key…"); shortcut: "E"; enabled: desktop.model.counts.selected === 1 && !!desktop.view.local; onTriggered: desktop.action("edit") }
    Action { id: cartAction; text: qsTr("Add to store carts…"); shortcut: "C"; enabled: root.hasSelection && !desktop.busy && !desktop.view.local; onTriggered: desktop.action("cart") }
    Action { id: removeAction; text: qsTr("Remove from playlist…"); shortcut: "X"; enabled: root.hasSelection && root.remoteView; onTriggered: desktop.action("remove") }
    // Space always belongs to playback: it pauses or resumes the loaded track, whatever is selected;
    // only with nothing loaded does it start the selected track. Enter starts the selected track.
    Action { id: playAction; text: qsTr("Play / pause"); shortcut: "Space"; enabled: root.loaded || root.hasSelection; onTriggered: root.playPause() }
    Action { id: stopAction; text: qsTr("Stop"); shortcut: "S"; enabled: root.loaded; onTriggered: desktop.transport("stop", 0) }
    Action { id: previousAction; text: qsTr("Previous track"); shortcut: "P"; enabled: root.loaded; onTriggered: desktop.step(-1) }
    Action { id: nextAction; text: qsTr("Next track"); shortcut: "N"; enabled: root.loaded; onTriggered: desktop.step(1) }
    Action { id: seekBackAction; text: qsTr("Seek backward 10 s"); shortcut: "["; enabled: root.loaded; onTriggered: root.nudge(-10) }
    Action { id: seekForwardAction; text: qsTr("Seek forward 10 s"); shortcut: "]"; enabled: root.loaded; onTriggered: root.nudge(10) }
    Action { id: volumeDownAction; text: qsTr("Volume down"); shortcut: "-"; onTriggered: root.changeVolume(-.05) }
    Action { id: volumeUpAction; text: qsTr("Volume up"); shortcut: "="; onTriggered: root.changeVolume(.05) }
    Action { id: muteAction; text: qsTr("Mute"); shortcut: "M"; onTriggered: root.toggleMute() }
    // Shift+[ and Shift+] type { and } on most layouts, which is what a shortcut sees.
    Action { id: seekBackLongAction; text: qsTr("Seek backward 30 s"); shortcut: "{"; enabled: root.loaded; onTriggered: root.nudge(-30) }
    Action { id: seekForwardLongAction; text: qsTr("Seek forward 30 s"); shortcut: "}"; enabled: root.loaded; onTriggered: root.nudge(30) }
    Action { id: dropAction; text: qsTr("Jump to drop"); shortcut: "J"; enabled: root.loaded && root.dropTime >= 0; onTriggered: root.jumpToDrop() }
    // Either, or neither: a track then starts at its beginning.
    Action { id: previewDropAction; text: qsTr("Start previews at the drop"); checkable: true; checked: root.previewStart === "drop"; onTriggered: root.previewStart = checked ? "drop" : "" }
    Action { id: previewThirtyAction; text: qsTr("Start previews at 30 %"); checkable: true; checked: root.previewStart === "30"; onTriggered: root.previewStart = checked ? "30" : "" }
    // 1-9 jump to 10-90 % of the track and 0 to its start; Ctrl+1-4 set hot cues, Alt+1-4 jump to them.
    readonly property bool seekKeys: loaded && !dialogOpen
    Shortcut { sequence: "1"; enabled: root.seekKeys; onActivated: root.jumpTo(.1) }
    Shortcut { sequence: "2"; enabled: root.seekKeys; onActivated: root.jumpTo(.2) }
    Shortcut { sequence: "3"; enabled: root.seekKeys; onActivated: root.jumpTo(.3) }
    Shortcut { sequence: "4"; enabled: root.seekKeys; onActivated: root.jumpTo(.4) }
    Shortcut { sequence: "5"; enabled: root.seekKeys; onActivated: root.jumpTo(.5) }
    Shortcut { sequence: "6"; enabled: root.seekKeys; onActivated: root.jumpTo(.6) }
    Shortcut { sequence: "7"; enabled: root.seekKeys; onActivated: root.jumpTo(.7) }
    Shortcut { sequence: "8"; enabled: root.seekKeys; onActivated: root.jumpTo(.8) }
    Shortcut { sequence: "9"; enabled: root.seekKeys; onActivated: root.jumpTo(.9) }
    Shortcut { sequence: "0"; enabled: root.seekKeys; onActivated: root.jumpTo(0) }
    Shortcut { sequence: "Ctrl+1"; enabled: root.seekKeys; onActivated: root.setCue(0) }
    Shortcut { sequence: "Ctrl+2"; enabled: root.seekKeys; onActivated: root.setCue(1) }
    Shortcut { sequence: "Ctrl+3"; enabled: root.seekKeys; onActivated: root.setCue(2) }
    Shortcut { sequence: "Ctrl+4"; enabled: root.seekKeys; onActivated: root.setCue(3) }
    Shortcut { sequence: "Alt+1"; enabled: root.seekKeys; onActivated: root.jumpCue(0) }
    Shortcut { sequence: "Alt+2"; enabled: root.seekKeys; onActivated: root.jumpCue(1) }
    Shortcut { sequence: "Alt+3"; enabled: root.seekKeys; onActivated: root.jumpCue(2) }
    Shortcut { sequence: "Alt+4"; enabled: root.seekKeys; onActivated: root.jumpCue(3) }
    Action { id: addPlaylistAction; text: qsTr("Add playlist…"); shortcut: "A"; onTriggered: desktop.action("dig") }
    Action { id: addFolderAction; text: qsTr("Add folder…"); shortcut: "Ctrl+O"; onTriggered: folderDialog.open() }
    Action { id: deletePlaylistAction; text: qsTr("Delete playlist…"); shortcut: "Shift+X"; enabled: !!desktop.view.source; onTriggered: desktop.deletePlaylist(desktop.view.source) }
    Action { id: searchAction; text: qsTr("Search"); shortcut: "Ctrl+F"; onTriggered: { search.forceActiveFocus(); search.selectAll() } }
    // H switches the status filter between every track and the unhandled ones.
    Action { id: hideAction; text: qsTr("Hide handled"); shortcut: "H"; onTriggered: root.setStatusFilter(root.statusFilter === "unhandled" ? "" : "unhandled") }

    // The menu bar holds what has no button elsewhere, grouped by what it acts on, plus the
    // transport, so every playback key can be found from the menus.
    menuBar: MenuBar {
        AppMenu {
            title: qsTr("Library")
            Action { text: qsTr("Refresh"); shortcut: "R"; enabled: !desktop.busy && (root.remoteView || root.folderView); onTriggered: desktop.action("refresh") }
            AppMenu {
                id: recentMenu
                title: qsTr("Open recent")
                AppMenuItem { text: qsTr("No recent items"); enabled: false; visible: root.recentEntries.length === 0 }
                Instantiator {
                    model: root.recentEntries
                    delegate: AppMenuItem {
                        required property var modelData
                        text: modelData.title; tip: modelData.kind === "folder" ? modelData.target : ""
                        onTriggered: modelData.kind === "folder" ? desktop.openFolder(modelData.target, 0) : desktop.load(modelData.target)
                    }
                    onObjectAdded: (index, object) => recentMenu.insertItem(index, object)
                    onObjectRemoved: (index, object) => recentMenu.removeItem(object)
                }
            }
            AppMenuSection { title: qsTr("Import") }
            Action { text: qsTr("Import profile playlists…"); property string tip: qsTr("Add every playlist of a SoundCloud profile to the sidebar")
                     enabled: !desktop.busy; onTriggered: desktop.action("profile") }
            Action { text: qsTr("Import saved summary…"); property string tip: qsTr("Show the tracks of a JSON summary exported earlier")
                     enabled: !desktop.busy; onTriggered: desktop.action("import_summary") }
            Action { text: qsTr("Restore removed tracks…"); property string tip: qsTr("Bring back the tracks removed from this playlist")
                     enabled: root.remoteView && !desktop.busy; onTriggered: desktop.action("restore") }
            AppMenuSection { title: qsTr("Folder") }
            Action { text: qsTr("Pin folder"); property string tip: qsTr("Keep this folder in the sidebar"); enabled: root.folderView; onTriggered: desktop.action("pin") }
            Action { text: qsTr("Analyze folder"); property string tip: qsTr("Detect BPM and key of every file in this folder")
                     enabled: root.folderView && !desktop.busy; onTriggered: desktop.action("analyze_folder") }
            Action { text: qsTr("Scan local library"); property string tip: qsTr("Find these tracks among the files in your scan folders")
                     enabled: !desktop.busy; onTriggered: desktop.action("scan") }
            AppMenuSection { title: qsTr("Export") }
            Action { text: qsTr("Resume interrupted export…"); property string tip: qsTr("Finish an audio export to a folder that was cut short")
                     enabled: !desktop.busy; onTriggered: desktop.action("resume") }
            AppSeparator {}
            Action { text: qsTr("Quit"); shortcut: "Ctrl+Q"; onTriggered: root.close() }
        }
        AppMenu {
            title: qsTr("Tracks")
            Action { id: selectAllAction; text: qsTr("Select all"); shortcut: "Ctrl+A"; onTriggered: desktop.model.selectAll() }
            Action { text: qsTr("Select none"); shortcut: "Ctrl+Shift+A"; enabled: root.hasSelection; onTriggered: desktop.model.clearSelection() }
            Action { text: qsTr("Invert selection"); shortcut: "Ctrl+I"; enabled: root.hasRows; onTriggered: desktop.model.invertSelection() }
            Action { text: qsTr("Undo status change"); shortcut: "Ctrl+Z"; onTriggered: desktop.action("undo") }
            AppSeparator {}
            Action { text: qsTr("Open all visible links"); shortcut: "Shift+O"; enabled: root.hasRows && !desktop.busy && !desktop.view.local; onTriggered: desktop.action("open", true) }
            Action { text: qsTr("Download all visible"); shortcut: "Shift+D"; enabled: root.hasRows && !desktop.busy && !desktop.view.local; onTriggered: desktop.action("download", true) }
            Action { text: qsTr("Add all visible to store carts…"); shortcut: "Shift+C"; enabled: root.hasRows && !desktop.busy && !desktop.view.local; onTriggered: desktop.action("cart", true) }
            AppSeparator {}
            Action { text: qsTr("Save local playlist…"); enabled: root.hasSelection && desktop.view.local; onTriggered: desktop.action("save_playlist") }
            Action { text: qsTr("Export links…"); shortcut: "Shift+E"; enabled: root.hasRows && !desktop.view.local; onTriggered: desktop.action("summary", !root.hasSelection) }
        }
        // The player's own Actions: one shortcut each, wherever it is shown.
        AppMenu {
            title: qsTr("Playback")
            AppMenuItem { action: playAction }
            AppMenuItem { action: stopAction }
            AppSeparator {}
            AppMenuItem { action: previousAction }
            AppMenuItem { action: nextAction }
            AppSeparator {}
            AppMenuItem { action: seekBackAction }
            AppMenuItem { action: seekForwardAction }
            AppMenuItem { action: seekBackLongAction }
            AppMenuItem { action: seekForwardLongAction }
            AppMenuItem { action: dropAction }
            AppSeparator {}
            AppMenuItem { action: previewDropAction }
            AppMenuItem { action: previewThirtyAction }
            AppSeparator {}
            AppMenuItem { action: volumeDownAction }
            AppMenuItem { action: volumeUpAction }
            AppMenuItem { action: muteAction }
        }
        AppMenu {
            title: qsTr("View")
            // Checked follows the state; a trigger sets the state from the new check, never toggles it again.
            Action { id: sidebarAction; text: qsTr("Show sidebar"); shortcut: "Ctrl+B"; checkable: true; checked: root.sidebarVisible; onTriggered: root.sidebarVisible = checked }
            Action { text: qsTr("Full screen"); shortcut: "F11"; checkable: true; checked: root.visibility === Window.FullScreen
                     onTriggered: if (checked) { root.windowedVisibility = root.visibility; root.visibility = Window.FullScreen } else root.visibility = root.windowedVisibility }
            AppMenu {
                id: viewColumnMenu
                title: qsTr("Columns")
                Instantiator {
                    model: root.allColumns.length
                    delegate: AppMenuItem {
                        required property int index
                        objectName: "viewColumn-" + index
                        text: (desktop.ready, desktop.model.headerName(index))
                        checkable: true; checked: root.hiddenColumns.indexOf(index) < 0
                        enabled: index !== root.titleColumn && root.columnAvailable(index)
                        onTriggered: root.toggleColumn(index)
                    }
                    onObjectAdded: (index, object) => viewColumnMenu.insertItem(index, object)
                    onObjectRemoved: (index, object) => viewColumnMenu.removeItem(object)
                }
            }
            Action { text: qsTr("Reset layout"); property string tip: qsTr("Default column widths and order, and sidebar width"); onTriggered: root.resetLayout() }
            AppSeparator {}
            AppMenu {
                title: qsTr("Language")
                ActionGroup { id: languageGroup }
                Action { text: "Deutsch"; checkable: true; checked: root.languageCode === "de"; ActionGroup.group: languageGroup; onTriggered: { root.languageCode = "de"; desktop.language("de") } }
                Action { text: "English"; checkable: true; checked: root.languageCode === "en"; ActionGroup.group: languageGroup; onTriggered: { root.languageCode = "en"; desktop.language("en") } }
                Action { text: "Español"; checkable: true; checked: root.languageCode === "es"; ActionGroup.group: languageGroup; onTriggered: { root.languageCode = "es"; desktop.language("es") } }
                Action { text: "Français"; checkable: true; checked: root.languageCode === "fr"; ActionGroup.group: languageGroup; onTriggered: { root.languageCode = "fr"; desktop.language("fr") } }
                Action { text: "Italiano"; checkable: true; checked: root.languageCode === "it"; ActionGroup.group: languageGroup; onTriggered: { root.languageCode = "it"; desktop.language("it") } }
                Action { text: "Polski"; checkable: true; checked: root.languageCode === "pl"; ActionGroup.group: languageGroup; onTriggered: { root.languageCode = "pl"; desktop.language("pl") } }
                Action { text: "Русский"; checkable: true; checked: root.languageCode === "ru"; ActionGroup.group: languageGroup; onTriggered: { root.languageCode = "ru"; desktop.language("ru") } }
            }
            AppMenu {
                title: qsTr("Theme")
                ActionGroup { id: themeGroup }
                Action { text: qsTr("System"); checkable: true; checked: root.themeChoice === "system"; ActionGroup.group: themeGroup; onTriggered: root.themeChoice = "system" }
                Action { text: qsTr("Dark"); checkable: true; checked: root.themeChoice === "dark"; ActionGroup.group: themeGroup; onTriggered: root.themeChoice = "dark" }
                Action { text: qsTr("Light"); checkable: true; checked: root.themeChoice === "light"; ActionGroup.group: themeGroup; onTriggered: root.themeChoice = "light" }
                Action { text: qsTr("High contrast"); checkable: true; checked: root.themeChoice === "contrast"; ActionGroup.group: themeGroup; onTriggered: root.themeChoice = "contrast" }
            }
            // Grouped by the first colour's family; each entry shows its two neon colours.
            AppMenu {
                title: qsTr("Colours")
                ActionGroup { id: colorGroup }
                PairAction { pair: "magenta-blue"; text: qsTr("Magenta and blue") }
                PairAction { pair: "crimson-ice"; text: qsTr("Crimson and ice blue") }
                PairAction { pair: "rose-mint"; text: qsTr("Rose and mint") }
                AppSeparator {}
                PairAction { pair: "purple-orange"; text: qsTr("Purple and orange") }
                PairAction { pair: "violet-gold"; text: qsTr("Violet and gold") }
                PairAction { pair: "indigo-lime"; text: qsTr("Indigo and lime") }
                AppSeparator {}
                PairAction { pair: "cyan-magenta"; text: qsTr("Cyan and magenta") }
                PairAction { pair: "blue-orange"; text: qsTr("Blue and orange") }
                PairAction { pair: "sky-lemon"; text: qsTr("Sky and lemon") }
                PairAction { pair: "teal-coral"; text: qsTr("Teal and coral") }
                AppSeparator {}
                PairAction { pair: "green-yellow"; text: qsTr("Green and yellow") }
                PairAction { pair: "emerald-pink"; text: qsTr("Emerald and pink") }
                PairAction { pair: "jade-raspberry"; text: qsTr("Jade and raspberry") }
                PairAction { pair: "lime-violet"; text: qsTr("Lime and violet") }
                PairAction { pair: "acid-ultraviolet"; text: qsTr("Acid and ultraviolet") }
                AppSeparator {}
                PairAction { pair: "gold-chrome"; text: qsTr("Gold and chrome") }
                PairAction { pair: "amber-teal"; text: qsTr("Amber and teal") }
                PairAction { pair: "copper-cyan"; text: qsTr("Copper and cyan") }
            }
            AppMenu {
                title: qsTr("Key notation")
                ActionGroup { id: notationGroup }
                Action { text: qsTr("Camelot (8A)"); checkable: true; checked: root.keyNotation === "camelot"; ActionGroup.group: notationGroup; onTriggered: root.keyNotation = "camelot" }
                Action { text: qsTr("Classic (Am)"); checkable: true; checked: root.keyNotation === "classic"; ActionGroup.group: notationGroup; onTriggered: root.keyNotation = "classic" }
            }
            AppSeparator {}
            Action { text: qsTr("Spectral waveform"); checkable: true; checked: root.spectral; onTriggered: root.spectral = checked }
            Action { text: qsTr("Vinyl artwork"); checkable: true; checked: root.vinylArtwork; onTriggered: root.vinylArtwork = checked }
            AppSeparator {}
            Action { id: animationsAction; text: qsTr("Animations"); checkable: true; checked: root.animations; onTriggered: root.animations = checked }
            Action { text: qsTr("Compact rows"); checkable: true; checked: root.compactRows; onTriggered: root.compactRows = checked }
        }
        AppMenu {
            title: qsTr("Settings")
            // The account and the browser come from the backend; a refresh on opening catches a browser that connected since.
            onOpened: desktop.action("account")
            Action { id: preferencesAction; text: qsTr("Preferences…"); shortcut: "Shift+S"; property string keys: "Shift+S, Ctrl+,"; onTriggered: desktop.action("settings") }
            AppSeparator {}
            Action {
                objectName: "accountAction"
                readonly property bool signedIn: !!desktop.account.signedIn
                text: !signedIn ? qsTr("Sign in to SoundCloud…")
                    : desktop.account.name ? qsTr("Signed in as %1 — Sign out…").arg(desktop.account.name) : qsTr("Signed in — Sign out…")
                enabled: !desktop.busy
                onTriggered: desktop.action(signedIn ? "logout" : "login")
            }
            // Always there: a second browser can be connected, and Check connection reached, while one is.
            Action { text: qsTr("Connect browser…"); onTriggered: desktop.action("connect_browser") }
            Action {
                readonly property string browser: desktop.account.browser || ""
                text: browser ? qsTr("Connected: %1 — Disconnect…").arg(browser) : qsTr("No browser connected")
                // Disconnecting closes the browser's channel, so not in the middle of a job.
                enabled: !!browser && !desktop.busy
                onTriggered: desktop.action("disconnect_browsers")
            }
        }
        AppMenu {
            title: qsTr("Help")
            Action { id: helpAction; text: qsTr("Keyboard shortcuts"); shortcut: "?"; property string keys: "F1, ?"; onTriggered: { helpText.text = root.shortcutList(); helpDialog.open() } }
            Action { text: qsTr("Messages"); onTriggered: messageLog.open() }
            Action { text: qsTr("Diagnostics"); onTriggered: desktop.action("logs") }
            AppSeparator {}
            Action { text: qsTr("Open data folder"); property string tip: qsTr("The library database and import reports"); onTriggered: desktop.revealFolder("data") }
            Action { text: qsTr("Open log folder"); property string tip: qsTr("Attach the log to a bug report"); onTriggered: desktop.revealFolder("logs") }
            Action { text: qsTr("Check for updates"); property string tip: qsTr("Opens the releases page in your browser"); onTriggered: desktop.action("updates") }
            AppSeparator {}
            Action { text: qsTr("About dj-digger…"); onTriggered: desktop.action("about") }
        }
    }
    Shortcut { sequence: "F1"; onActivated: helpAction.trigger() }
    Shortcut { sequence: "Ctrl+,"; onActivated: preferencesAction.trigger() }
    Shortcut { sequence: "/"; enabled: !root.dialogOpen; onActivated: { search.forceActiveFocus(); search.selectAll() } }
    Shortcut { sequence: "Escape"; enabled: !root.dialogOpen; onActivated: root.clearFilters() }

    // Every menu shares one row layout: a fixed check column, the label, the
    // shortcut in the muted color and a submenu arrow. Basic never shows
    // shortcuts and indents only checkable items, which misaligned the labels.
    component AppMenuItem: MenuItem {
        id: menuItem
        property string keys: ""
        // Why a disabled entry is disabled, shown after its label; destructive entries use the danger colour.
        property string reason: ""
        property bool destructive: false
        // The highlight already marks the keyboard's entry; the window's focus ring stays off menus.
        readonly property bool focusRing: false
        // An Action may carry extra properties: keys (every shortcut, for display), tip (a tooltip)
        // and pair (a colour pair, shown as a swatch).
        readonly property string shortcutText: keys || (action && action.keys ? action.keys : "") || (action && action.shortcut ? String(action.shortcut) : "")
        property string tip: action && action.tip ? action.tip : ""
        readonly property var swatch: action && action.pair ? root.colorPairs[action.pair][root.dark ? "dark" : "light"].slice(2) : []
        readonly property real trailingWidth: shortcutLabel.visible ? shortcutLabel.implicitWidth : swatchChip.visible ? swatchChip.implicitWidth : 0
        // Plain Text measures synchronously, so the menu can size itself before it opens.
        implicitWidth: leftPadding + labelText.implicitWidth + (trailingWidth ? 28 + trailingWidth : 0) + rightPadding
        ToolTip.visible: hovered && tip.length > 0; ToolTip.text: tip; ToolTip.delay: root.tipDelay
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
                width: Math.min(implicitWidth, parent.width - (menuItem.trailingWidth ? menuItem.trailingWidth + 28 : 0))
                text: menuItem.text + (!menuItem.enabled && menuItem.reason ? " (" + menuItem.reason + ")" : "")
                textFormat: Text.PlainText; elide: Text.ElideRight; font: menuItem.font
                // A disabled entry that says why stays readable: the reason is the point of it.
                color: !menuItem.enabled ? (menuItem.reason ? root.muted : root.disabledFg) : menuItem.destructive ? root.danger : root.fg
            }
            Text {
                id: shortcutLabel
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                visible: text.length > 0; text: menuItem.shortcutText; textFormat: Text.PlainText
                font.pixelSize: root.fontCaption; color: menuItem.enabled ? root.muted : root.disabledFg
            }
            Row {
                id: swatchChip
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                visible: menuItem.swatch.length === 2 && !shortcutLabel.visible
                Repeater {
                    model: menuItem.swatch
                    Rectangle { required property color modelData; width: 12; height: 12; color: modelData }
                }
            }
        }
        background: Rectangle { x: 3; width: menuItem.width - 6; height: menuItem.height; radius: 3; color: menuItem.highlighted ? root.hoverSurface : "transparent" }
    }
    // Separators stay quiet: the stronger border colour is for controls, not for dividing lists.
    component AppSeparator: MenuSeparator {
        height: visible ? implicitHeight : 0
        contentItem: Rectangle { implicitWidth: 188; implicitHeight: 1; color: root.alternate }
    }
    // A quiet heading inside a menu; not an entry, so it cannot be picked.
    component AppMenuSection: MenuSeparator {
        id: menuSection
        property string title
        height: visible ? implicitHeight : 0
        leftPadding: 14; topPadding: 8; bottomPadding: 2
        contentItem: Text {
            text: menuSection.title; textFormat: Text.PlainText
            font.bold: true; font.pixelSize: root.fontSmall; font.letterSpacing: 1; font.capitalization: Font.AllUppercase; color: root.muted
        }
    }
    // One entry of View → Colours.
    component PairAction: Action {
        property string pair
        checkable: true; checked: root.colorPair === pair; ActionGroup.group: colorGroup
        onTriggered: root.colorPair = pair
    }
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
        property string keys: action && action.shortcut ? String(action.shortcut) : ""
        display: AbstractButton.IconOnly
        icon.width: root.iconSmall; icon.height: root.iconSmall; icon.color: enabled ? root.fg : root.disabledFg
        padding: 4
        background: Rectangle { radius: 4; color: flatButton.down ? root.pressedSurface : flatButton.hovered ? root.hoverSurface : "transparent" }
        Accessible.name: text
        ToolTip.visible: hovered; ToolTip.delay: root.tipDelay; ToolTip.text: text + (keys ? " (" + keys + ")" : "")
    }
    // A sidebar section's centred, uppercase name: a click folds the section away or back,
    // the button on the right (or a right click) opens the section's menu.
    component SidebarHeader: ItemDelegate {
        id: sidebarHeader
        property string section
        property Menu menu
        readonly property bool open: root.sectionOpen(section)
        width: parent ? parent.width : 0; height: 30
        padding: 0; leftPadding: 6; rightPadding: 6
        focusPolicy: Qt.StrongFocus
        background: Rectangle { color: sidebarHeader.hovered ? root.hoverSurface : "transparent" }
        contentItem: RowLayout {
            spacing: 4
            Item {
                implicitWidth: 20; implicitHeight: 20
                ColorImage {
                    anchors.centerIn: parent; width: 10; height: 10; sourceSize: Qt.size(10, 10)
                    source: "icons/chevron.svg"; rotation: sidebarHeader.open ? 90 : 0; color: root.muted
                }
            }
            Label {
                Layout.fillWidth: true; text: sidebarHeader.text
                horizontalAlignment: Text.AlignHCenter; textFormat: Text.PlainText; elide: Text.ElideRight
                font.bold: true; font.pixelSize: root.fontSmall; font.letterSpacing: 1; font.capitalization: Font.AllUppercase; color: root.muted
            }
            FlatButton {
                implicitWidth: 20; implicitHeight: 20; padding: 0; focusPolicy: Qt.NoFocus
                icon.source: "icons/more.svg"; icon.color: root.muted
                text: qsTr("%1 menu").arg(sidebarHeader.text)
                onClicked: sidebarHeader.menu.popup(this, 0, height)
            }
        }
        Accessible.name: text
        Accessible.description: open ? qsTr("Expanded") : qsTr("Collapsed")
        onClicked: root.toggleSection(section)
        Keys.onMenuPressed: menu.popup(this, 0, height)
        TapHandler { acceptedButtons: Qt.RightButton; onTapped: sidebarHeader.menu.popup() }
    }
    // Tracks in the open folder: known only for the folder whose page is loaded.
    component FolderCount: Label {
        text: desktop.folder.total || ""
        visible: !!desktop.folder.total
        font.pixelSize: root.fontSmall; font.features: root.tabular; color: root.selectionText
    }
    // Marks a folder the user pinned (Add folder…, Pin folder).
    component PinMark: ColorImage {
        property bool selected: false
        width: 12; height: 12; sourceSize: Qt.size(12, 12); source: "icons/pin.svg"
        color: selected ? root.selectionText : root.muted
        Accessible.name: qsTr("Pinned")
    }
    // A quiet full-width row with a centred "+" right under the last entry of a sidebar list, shaped
    // like the rows above it and only lit on hover; the name stays in the tooltip.
    component AddButton: Item {
        id: addButton
        property alias text: addControl.text
        property alias buttonName: addControl.objectName
        signal add()
        width: parent ? parent.width : 0; implicitHeight: 32
        FlatButton {
            id: addControl
            anchors.fill: parent; anchors.topMargin: 2; anchors.bottomMargin: 2
            icon.source: "icons/plus.svg"; icon.color: hovered ? root.fg : root.muted
            background: Rectangle { color: addControl.down ? root.pressedSurface : addControl.hovered ? root.hoverSurface : "transparent" }
            onClicked: addButton.add()
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
            font.bold: true; font.pixelSize: root.fontLarge; color: root.fg
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
        // The same bars in the pair's neon colours, for the kick light.
        property bool neon: false
        onPaint: waveform.paintBars(getContext("2d"), width, height, played, neon)
        // A resize repaints once it settles; a window or sidebar drag would otherwise paint every frame.
        Timer { id: settle; interval: 50; onTriggered: bars.requestPaint() }
        onWidthChanged: settle.restart()
        onHeightChanged: settle.restart()
        onVisibleChanged: requestPaint()
        Connections { target: waveform; function onRepaint() { requestPaint() } }
        Connections { target: root; function onPairColorsChanged() { requestPaint() } function onSpectralColorsChanged() { requestPaint() } function onSpectralChanged() { requestPaint() } }
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
        // The ring is the text colour, never a key hue, so it cannot be mistaken for a key's tint.
        border.width: match ? 2 : code ? 1 : 0
        border.color: match ? root.fg : root.keyTint(code, .8)
        Label {
            id: chipLabel
            anchors.centerIn: parent; textFormat: Text.PlainText; font.pixelSize: root.fontCaption; font.features: root.tabular
            font.bold: chip.match; color: root.fg
        }
        ToolTip.visible: match && chipHover.hovered; ToolTip.delay: root.tipDelay; ToolTip.text: qsTr("Mixes with the playing track")
        HoverHandler { id: chipHover }
    }
    // Two colours laid out from the track hash: blended along a line at a random angle,
    // each end held for a random stretch, with one of them pooled around a random spot, so
    // no two tracks share a backdrop. Painted once per track, pair, theme and size.
    component Backdrop: Canvas {
        property int code: 0
        property color first
        property color second
        onCodeChanged: requestPaint()
        onFirstChanged: requestPaint()
        onSecondChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            let ctx = getContext("2d"), random = root.seeded(code)
            ctx.reset()
            ctx.beginPath(); ctx.roundedRect(0, 0, width, height, 6, 6); ctx.clip()
            let angle = random() * Math.PI * 2, dx = Math.cos(angle) * width / 2, dy = Math.sin(angle) * height / 2
            let line = ctx.createLinearGradient(width / 2 - dx, height / 2 - dy, width / 2 + dx, height / 2 + dy)
            line.addColorStop(random() * .35, first)
            line.addColorStop(.65 + random() * .35, second)
            ctx.fillStyle = line
            ctx.fillRect(0, 0, width, height)
            let pool = random() < .5 ? first : second, x = random() * width, y = random() * height
            let spot = ctx.createRadialGradient(x, y, 0, x, y, width * (.3 + random() * .4))
            spot.addColorStop(0, pool)
            spot.addColorStop(1, Qt.rgba(pool.r, pool.g, pool.b, 0))
            ctx.fillStyle = spot
            ctx.fillRect(0, 0, width, height)
        }
    }
    // A record on a backdrop laid out from the track key; the label carries the track's
    // artwork (or the default cover) when there is one, turning with the record while it plays.
    // Until the track is ready the sleeve and label are white and the record blank; then the
    // colours, the picture and the code fade in.
    component Cover: Rectangle {
        id: cover
        property string seed: ""
        property bool spinning: false
        property bool ready: true
        property string artwork: ""
        // The same picture as a plain square, shown over the whole cover instead of the record.
        property string square: ""
        property bool vinyl: true
        // The label's share of the record: large enough to make the artwork out.
        readonly property real labelSize: .42
        // Nothing loaded: the pair's colours behind a plain record with a white label and no code.
        property bool idle: false
        // A binding at first; the handler takes over from the first change.
        property real reveal: ready ? 1 : 0
        onReadyChanged: {
            revealAnimation.stop()
            if (ready && root.motion) revealAnimation.restart(); else reveal = ready ? 1 : 0
        }
        NumberAnimation { id: revealAnimation; target: cover; property: "reveal"; from: 0; to: 1; duration: 600; easing.type: Easing.InOutQuad }
        readonly property int hash: {
            let h = 7
            for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) | 0
            return Math.abs(h)
        }
        // Always the two colours of the chosen pair; only their layout comes from the track.
        readonly property color first: root.waveRestFoot
        readonly property color second: root.waveRestTop
        radius: 6; clip: true; color: "transparent"
        Backdrop { objectName: "coverBackdrop"; anchors.fill: parent; code: cover.hash; first: cover.first; second: cover.second }
        // The backdrop glows on each kick.
        Backdrop {
            objectName: "coverGlow"
            anchors.fill: parent; opacity: root.glow
            code: cover.hash; first: root.peakColor(cover.first); second: root.peakColor(cover.second)
        }
        Rectangle { objectName: "blankSleeve"; anchors.fill: parent; radius: parent.radius; color: "white"; opacity: cover.idle ? 0 : 1 - cover.reveal }
        Item {
            id: disc
            width: parent.width * .84; height: width; anchors.centerIn: parent
            // Near-black vinyl; the label is darkened so it does not outshine the record.
            Rectangle { anchors.fill: parent; radius: width / 2; color: "#0c0c0e" }
            Rectangle { anchors.centerIn: parent; width: disc.width * cover.labelSize; height: width; radius: width / 2; color: Qt.darker((cover.hash >> 5) % 2 ? cover.first : cover.second, 1.25) }
            // Already cut to a circle by the backend, shown as it is: no tint, no overlay.
            Image {
                objectName: "labelArtwork"
                anchors.centerIn: parent; width: disc.width * cover.labelSize; height: width
                source: cover.artwork; asynchronous: true; smooth: true; mipmap: true
                opacity: status === Image.Ready ? 1 : 0
                Behavior on opacity { enabled: root.motion; NumberAnimation { duration: 300 } }
            }
            Rectangle { objectName: "whiteLabel"; anchors.centerIn: parent; width: disc.width * cover.labelSize; height: width; radius: width / 2; color: "white"; opacity: cover.idle ? 1 : 1 - cover.reveal }
            // A barcode of the track: radial strokes and dots from the label to the rim, laid out
            // from the key hash. Painted once per track; turning the record costs no repaint.
            Canvas {
                objectName: "trackCode"
                anchors.fill: parent
                opacity: cover.idle ? 0 : cover.reveal
                property int code: cover.hash
                onCodeChanged: requestPaint()
                onWidthChanged: requestPaint()
                onPaint: {
                    let ctx = getContext("2d")
                    ctx.reset()
                    let random = root.seeded(code)
                    let centre = width / 2, inner = width * (cover.labelSize / 2 + .02), outer = width * .48
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
        // View → Vinyl artwork off: the picture itself, square and still, over the record.
        Image {
            objectName: "squareArtwork"
            anchors.fill: parent; fillMode: Image.PreserveAspectCrop
            visible: !cover.vinyl && !cover.idle
            source: visible ? cover.square : ""; asynchronous: true; smooth: true; mipmap: true
            opacity: status === Image.Ready ? 1 : 0
            Behavior on opacity { enabled: root.motion; NumberAnimation { duration: 300 } }
        }
    }
    // The waveform rests in deep, saturated tones and lights up in neon on a kick,
    // foot (raspberry side) to top (blue side). On the dark theme the neon is over
    // twice as bright as the rest, so a kick reads as light, not as a change of hue
    // (a light pastel rest looked lit and the kick looked like it went out), and
    // neither is pastel. On the light theme the kick is the more vivid. No colour is
    // a saturated red, and the rest colours keep 3:1 against the panel. The whole
    // played side lights up, so it is the small luminance step, not a small area,
    // that keeps a kick under the flash thresholds.
    // The pair chosen in View → Colours paints the waveform and the artwork backdrop:
    // rest foot, rest top, neon foot, neon top, per theme.
    property string colorPair: "magenta-blue"
    readonly property var colorPairs: ({
        "magenta-blue": { dark: ["#b8286a", "#3a5fd6", "#ff2fa8", "#1e9bff"], light: ["#ff2187", "#4d77ff", "#ff0094", "#42b1ff"] },
        "green-yellow": { dark: ["#1a8f45", "#a88d0a", "#2bff6e", "#ffe000"], light: ["#069d3e", "#a18606", "#00c43e", "#c1a900"] },
        "purple-orange": { dark: ["#8a2be2", "#b0660e", "#e52eff", "#ffa30a"], light: ["#a94dff", "#ca7208", "#e845ff", "#ef9500"] },
        "cyan-magenta": { dark: ["#0e8597", "#b8286a", "#00e5ff", "#ff2fa8"], light: ["#0795ab", "#ff2187", "#00bacf", "#ff0094"] },
        "crimson-ice": { dark: ["#b52f5c", "#067299", "#ff3399", "#00bbff"], light: ["#f83c7b", "#0892c4", "#ff369a", "#00b5f7"] },
        "gold-chrome": { dark: ["#997206", "#0863bf", "#ffbb00", "#0091ff"], light: ["#ad8107", "#0f86ff", "#d9a002", "#03b4ff"] },
        "violet-gold": { dark: ["#a708c7", "#997706", "#f700ff", "#ffc300"], light: ["#d60aff", "#a98307", "#ff00fe", "#d4a200"] },
        "teal-coral": { dark: ["#068a78", "#a14b27", "#00ffdd", "#ff8533"], light: ["#069985", "#d86738", "#00bfa5", "#ff8a3b"] },
        "emerald-pink": { dark: ["#068c49", "#bd176f", "#00ff80", "#ff14c8"], light: ["#069c51", "#ff0a8c", "#00c362", "#ff00c3"] },
        "jade-raspberry": { dark: ["#068c68", "#b22e6c", "#00ffbb", "#ff3392"], light: ["#069a72", "#ff1c87", "#00c08d", "#ff1288"] },
        "rose-mint": { dark: ["#b82862", "#068c5f", "#ff2ea4", "#00ffaa"], light: ["#fe2e82", "#069b69", "#ff0090", "#00c181"] },
        "lime-violet": { dark: ["#4c8a06", "#a109d9", "#88ff00", "#f700ff"], light: ["#549906", "#cd45ff", "#65be00", "#f93dff"] },
        "acid-ultraviolet": { dark: ["#718205", "#ac0fb8", "#ddff00", "#ef0aff"], light: ["#7e9106", "#ea0afb", "#9cb400", "#ff00ff"] },
        "amber-teal": { dark: ["#995906", "#058787", "#ff9000", "#00ffff"], light: ["#c77408", "#069797", "#fb8e00", "#00bcbc"] },
        "copper-cyan": { dark: ["#9c4f22", "#068599", "#ff822e", "#00ddff"], light: ["#d56a2b", "#0795ac", "#ff872b", "#00bad6"] },
        "blue-orange": { dark: ["#1354ed", "#9c5313", "#0a8dff", "#ff8a14"], light: ["#4c81fe", "#d06d17", "#45afff", "#ff8912"] },
        "sky-lemon": { dark: ["#066a9c", "#857c05", "#00aaff", "#ffee00"], light: ["#0890d3", "#948a06", "#00b3ff", "#b9ac00"] },
        "indigo-lime": { dark: ["#3556db", "#538705", "#338bff", "#99ff00"], light: ["#4d70ff", "#5d9706", "#3db1ff", "#71bd00"] }
    })
    readonly property var pairColors: colorPairs[colorPair][dark ? "dark" : "light"]
    // The browser extension's player panel wears the same pair and theme.
    onPairColorsChanged: desktop.browserLook(pairColors, dark)
    readonly property color waveRestFoot: pairColors[0]
    readonly property color waveRestTop: pairColors[1]
    readonly property color waveNeonFoot: pairColors[2]
    readonly property color waveNeonTop: pairColors[3]
    // View → Spectral waveform: warm lows, green mids and cool highs, mixed by each bar's shares.
    // None is a saturated red, and each keeps 3:1 against the panel, like the pairs.
    readonly property var spectralColors: dark ? ["#ff9a2e", "#5fd068", "#4f8dff"] : ["#b86e00", "#2f8a3a", "#2f62d6"]
    readonly property var spectralAnchors: spectralColors.map(value => Qt.color(value))
    function spectralColor(mix) {
        let c = spectralAnchors
        return Qt.rgba(c[0].r * mix[0] + c[1].r * mix[1] + c[2].r * mix[2], c[0].g * mix[0] + c[1].g * mix[1] + c[2].g * mix[2],
                       c[0].b * mix[0] + c[1].b * mix[1] + c[2].b * mix[2], 1)
    }
    function luminance(color) {
        let linear = c => c <= .04045 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4)
        return .2126 * linear(color.r) + .7152 * linear(color.g) + .0722 * linear(color.b)
    }
    // The same hue, more saturated and lighter: a kick makes a colour glow without
    // washing it out. A red (from 70 % of linear R+G+B) only lightens, and less, so
    // the bordeaux end stays under the WCAG 2.3.1 red-flash threshold. Bright hues
    // (green, yellow, cyan) gain luminance fast, so their step is scaled back to keep
    // the change under the 0.1 general-flash threshold.
    function peakColor(value) {
        let color = Qt.color(value)  // Artwork tones may be colour names as text.
        let linear = c => c <= .04045 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4)
        let r = linear(color.r), g = linear(color.g), b = linear(color.b)
        if (r / Math.max(1e-6, r + g + b) >= .7)
            return Qt.hsla(color.hslHue, color.hslSaturation, Math.min(1, color.hslLightness + (dark ? .02 : .03)), 1)
        let saturation = Math.min(1, color.hslSaturation + .3), lightness = Math.min(1, color.hslLightness + (dark ? .06 : .08))
        let gain = luminance(Qt.hsla(color.hslHue, saturation, lightness, 1)) - luminance(color), t = gain > .08 ? .08 / gain : 1
        return Qt.hsla(color.hslHue, color.hslSaturation + (saturation - color.hslSaturation) * t,
                       color.hslLightness + (lightness - color.hslLightness) * t, 1)
    }
    // mulberry32: the same seed always gives the same sequence, so a track always draws the same.
    function seeded(state) {
        state |= 0
        return function() {
            state = (state + 0x6D2B79F5) | 0
            let t = Math.imul(state ^ (state >>> 15), 1 | state)
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296
        }
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
            // A thin line between the sidebar and the tracks, lit while dragged or hovered;
            // a double click puts the sidebar back to its default width.
            handle: Item {
                implicitWidth: 6; implicitHeight: 6
                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter; width: 1; height: parent.height
                    color: parent.SplitHandle.pressed || parent.SplitHandle.hovered ? root.accent : root.alternate
                }
                TapHandler { onDoubleTapped: sidebar.SplitView.preferredWidth = 220 }
            }
            ColumnLayout {
                id: sidebar
                visible: root.sidebarVisible
                spacing: 0
                SplitView.preferredWidth: root.sidebarWidth; SplitView.minimumWidth: 160
                SplitView.maximumWidth: Math.max(160, root.width - 520)
                TextField {
                    id: sidebarFilter; objectName: "sidebarFilter"
                    Layout.fillWidth: true; Layout.margins: 6; Layout.bottomMargin: 2
                    placeholderText: qsTr("Filter playlists and folders")
                    selectByMouse: true
                    background: Rectangle {
                        implicitHeight: 30; radius: 6; color: root.bg
                        border.color: sidebarFilter.activeFocus ? root.accent : root.alternate
                    }
                    Keys.onEscapePressed: event => { text = ""; event.accepted = true }
                    Keys.onDownPressed: playlistList.forceActiveFocus()
                    Accessible.name: placeholderText
                }
                // Playlists and folders scroll together in one panel; each section folds away from its header.
                ScrollView {
                    id: sidebarScroll
                    Layout.fillWidth: true; Layout.fillHeight: true
                    contentWidth: availableWidth
                    Column {
                        width: sidebarScroll.availableWidth
                        SidebarHeader { objectName: "playlistsHeader"; section: "playlists"; text: qsTr("Playlists"); menu: playlistsSectionMenu }
                        ListView {
                            id: playlistList; objectName: "playlistList"
                            visible: root.sectionOpen("playlists")
                            width: parent.width; height: visible ? contentHeight : 0
                            // The panel scrolls, not the list; arrows and Enter still move through it.
                            interactive: false; keyNavigationEnabled: true; activeFocusOnTab: true
                            currentIndex: -1
                            model: desktop.playlists.filter(p => root.sidebarMatch(p.title))
                            onCurrentItemChanged: if (activeFocus && currentItem) root.revealInSidebar(currentItem)
                            Keys.onReturnPressed: if (currentItem) currentItem.open()
                            Keys.onEnterPressed: if (currentItem) currentItem.open()
                            Keys.onMenuPressed: if (currentItem) root.playlistMenuAt(currentItem.modelData.source)
                            delegate: ItemDelegate {
                                id: playlistItem
                                objectName: "playlist-" + modelData.source
                                required property var modelData
                                required property int index
                                readonly property bool local: modelData.source.startsWith("local-playlist:")
                                // A playlist still importing is listed at once, with a spinner; it opens when saved.
                                readonly property bool pending: !!modelData.pending
                                // A press held long enough on a touch screen opens the menu, never the playlist too.
                                property bool held: false
                                width: ListView.view.width; height: 28
                                padding: 0; leftPadding: 10; rightPadding: 8
                                highlighted: modelData.source === desktop.view.source
                                background: Rectangle {
                                    color: playlistItem.highlighted ? root.selection : playlistItem.hovered ? root.hoverSurface : "transparent"
                                    border.width: playlistDrop.containsDrag || (playlistItem.ListView.isCurrentItem && playlistList.activeFocus) ? 2 : 0
                                    border.color: root.accent
                                }
                                contentItem: RowLayout {
                                    spacing: 6
                                    ColorImage {
                                        visible: !playlistItem.pending
                                        width: 14; height: 14; sourceSize: Qt.size(14, 14)
                                        source: playlistItem.local ? "icons/playlist.svg" : "icons/cloud.svg"
                                        color: playlistItem.highlighted ? root.selectionText : root.muted
                                        Accessible.name: playlistItem.local ? qsTr("Local playlist") : qsTr("SoundCloud playlist")
                                    }
                                    BusyIndicator {
                                        visible: playlistItem.pending; running: visible
                                        implicitWidth: 16; implicitHeight: 16; padding: 0
                                        palette.dark: playlistItem.highlighted ? root.selectionText : root.accent
                                    }
                                    Label { Layout.fillWidth: true; text: modelData.title; textFormat: Text.PlainText; elide: Text.ElideRight; color: playlistItem.highlighted ? root.selectionText : root.fg }
                                    Label {
                                        objectName: "playlistCount"
                                        visible: !playlistItem.pending && modelData.count !== undefined
                                        text: modelData.count !== undefined ? modelData.count : ""
                                        font.pixelSize: root.fontSmall; font.features: root.tabular
                                        color: playlistItem.highlighted ? root.selectionText : root.muted
                                    }
                                }
                                Accessible.name: modelData.title
                                function open() { if (!pending) desktop.load(modelData.source) }
                                onPressed: held = false
                                onClicked: {
                                    playlistList.currentIndex = index
                                    playlistList.forceActiveFocus()
                                    if (held) held = false
                                    else open()
                                }
                                onPressAndHold: { held = true; root.playlistMenuAt(modelData.source) }
                                TapHandler { acceptedButtons: Qt.RightButton; onTapped: root.playlistMenuAt(playlistItem.modelData.source) }
                                // Tracks dragged from the table join a local playlist; SoundCloud playlists follow SoundCloud.
                                DropArea {
                                    id: playlistDrop
                                    anchors.fill: parent; keys: [root.trackDragKey]
                                    enabled: playlistItem.local && !playlistItem.pending
                                    onDropped: drop => { desktop.addToPlaylist(playlistItem.modelData.source); drop.accept(Qt.CopyAction) }
                                }
                            }
                            footer: AddButton { buttonName: "addPlaylist"; text: qsTr("Add playlist"); onAdd: desktop.action("dig") }
                        }
                        Label {
                            width: parent.width; padding: 8
                            visible: root.sectionOpen("playlists") && playlistList.count === 0
                            wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: root.muted
                            text: desktop.playlists.length > 0 ? qsTr("No playlists match the filter.") : qsTr("No playlists yet. Add a SoundCloud link to start digging.")
                        }
                        // Folders dropped here from the file manager are added like Add folder… does.
                        Item {
                            width: parent.width; height: folderSection.height
                            Column {
                                id: folderSection
                                width: parent.width
                                SidebarHeader { objectName: "foldersHeader"; section: "folders"; text: qsTr("Folders"); menu: foldersSectionMenu }
                                Column {
                                    width: parent.width
                                    visible: root.sectionOpen("folders")
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
                                            readonly property string name: modelData.kind === "downloads" ? qsTr("Downloads")
                                                : modelData.kind === "music" ? qsTr("Music")
                                                : modelData.path.split(/[\\/]/).filter(p => p).pop() || modelData.path
                                            visible: {
                                                const revision = desktop.directoryModel.revision
                                                return root.sidebarMatch(name) || !!root.sidebarQuery && desktop.folderMatches(desktop.directoryIndex(modelData.path), root.sidebarQuery)
                                            }
                                            onExpandableChanged: if (!expandable) expanded = false
                                            // The name opens the folder; one whose music is all in folders below also unfolds them.
                                            function open() {
                                                desktop.openFolder(modelData.path, 0)
                                                if (expandable && desktop.folderNeedsExpand(modelData.path)) expanded = true
                                            }
                                            ItemDelegate {
                                                id: rootItem
                                                objectName: "root-" + (folderRoot.modelData.kind === "custom" ? folderRoot.modelData.path : folderRoot.modelData.kind)
                                                property bool held: false
                                                width: parent.width; height: 28
                                                padding: 0; leftPadding: 6; rightPadding: 8
                                                focusPolicy: Qt.StrongFocus
                                                highlighted: root.folderView && desktop.samePath(desktop.folder.path, folderRoot.modelData.path)
                                                background: Rectangle {
                                                    color: rootItem.highlighted ? root.selection : rootItem.hovered ? root.hoverSurface : "transparent"
                                                }
                                                contentItem: RowLayout {
                                                    spacing: 4
                                                    // The chevron's place stays empty without subfolders, so every name lines up.
                                                    Item {
                                                        implicitWidth: 20; implicitHeight: 20
                                                        FlatButton {
                                                            objectName: "expand-" + folderRoot.modelData.kind
                                                            anchors.fill: parent; padding: 0
                                                            enabled: folderRoot.expandable; visible: enabled
                                                            text: folderRoot.expanded ? qsTr("Collapse") : qsTr("Expand")
                                                            icon.source: "icons/chevron.svg"; icon.width: 10; icon.height: 10
                                                            icon.color: rootItem.highlighted ? root.selectionText : root.muted
                                                            rotation: folderRoot.expanded ? 90 : 0
                                                            focusPolicy: Qt.NoFocus
                                                            Accessible.name: text + " " + folderRoot.name
                                                            onClicked: folderRoot.expanded = !folderRoot.expanded
                                                        }
                                                    }
                                                    FolderIcon { selected: rootItem.highlighted }
                                                    Label {
                                                        Layout.fillWidth: true; text: folderRoot.name
                                                        textFormat: Text.PlainText; elide: Text.ElideMiddle
                                                        color: rootItem.highlighted ? root.selectionText : root.fg
                                                    }
                                                    FolderCount { visible: rootItem.highlighted }
                                                    PinMark { visible: folderRoot.modelData.kind === "custom"; selected: rootItem.highlighted }
                                                }
                                                ToolTip.visible: hovered; ToolTip.delay: root.tipDelay; ToolTip.text: folderRoot.modelData.path
                                                Accessible.name: folderRoot.name
                                                Accessible.description: folderRoot.modelData.path
                                                onActiveFocusChanged: if (activeFocus) root.revealInSidebar(rootItem)
                                                onPressed: held = false
                                                onClicked: {
                                                    if (held) held = false
                                                    else folderRoot.open()
                                                }
                                                onPressAndHold: { held = true; root.folderMenuAt(folderRoot.modelData.path) }
                                                Keys.onReturnPressed: folderRoot.open()
                                                Keys.onEnterPressed: folderRoot.open()
                                                Keys.onRightPressed: if (folderRoot.expandable) folderRoot.expanded = true
                                                Keys.onLeftPressed: folderRoot.expanded = false
                                                // A keyboard move, so the focus ring shows on the row it reaches.
                                                Keys.onDownPressed: nextItemInFocusChain(true).forceActiveFocus(Qt.TabFocusReason)
                                                Keys.onUpPressed: nextItemInFocusChain(false).forceActiveFocus(Qt.TabFocusReason)
                                                Keys.onMenuPressed: root.folderMenuAt(folderRoot.modelData.path)
                                                TapHandler { acceptedButtons: Qt.RightButton; onTapped: root.folderMenuAt(folderRoot.modelData.path) }
                                            }
                                            TreeView {
                                                id: directoryTree
                                                objectName: "directoryTree-" + (folderRoot.modelData.kind === "custom" ? folderRoot.modelData.path : folderRoot.modelData.kind)
                                                width: parent.width
                                                // As tall as its rows: the sidebar panel scrolls, never the tree inside it.
                                                height: visible ? contentHeight : 0
                                                visible: folderRoot.expanded && folderRoot.expandable
                                                interactive: false; activeFocusOnTab: true
                                                clip: true; model: desktop.directoryModel
                                                rootIndex: desktop.directoryIndex(folderRoot.modelData.path)
                                                editTriggers: TableView.NoEditTriggers
                                                selectionBehavior: TableView.SelectRows
                                                selectionModel: ItemSelectionModel { model: desktop.directoryModel }
                                                columnWidthProvider: column => column === 0 ? width : 0
                                                // The sidebar filter hides folders whose names (and those below, as far as read) miss it.
                                                rowHeightProvider: row => !root.sidebarQuery || desktop.folderMatches(index(row, 0), root.sidebarQuery) ? 28 : 0
                                                Connections { target: root; function onSidebarQueryChanged() { directoryTree.forceLayout() } }
                                                onCurrentRowChanged: if (activeFocus && itemAtCell(Qt.point(0, currentRow))) root.revealInSidebar(itemAtCell(Qt.point(0, currentRow)))
                                                delegate: TreeViewDelegate {
                                                    id: directoryDelegate
                                                    objectName: "directory-" + filePath
                                                    required property string fileName
                                                    required property string filePath
                                                    property bool held: false
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
                                                    background: Rectangle {
                                                        color: directoryDelegate.highlighted ? root.selection : directoryDelegate.hovered ? root.hoverSurface : root.bg
                                                        border.width: directoryDelegate.current && directoryTree.activeFocus ? 2 : 0
                                                        border.color: root.accent
                                                    }
                                                    contentItem: RowLayout {
                                                        spacing: 6
                                                        FolderIcon { selected: directoryDelegate.highlighted }
                                                        Label { Layout.fillWidth: true; text: directoryDelegate.fileName; textFormat: Text.PlainText; elide: Text.ElideRight; color: directoryDelegate.highlighted ? root.selectionText : root.fg }
                                                        FolderCount { visible: directoryDelegate.highlighted }
                                                        PinMark {
                                                            visible: (desktop.pinnedFolders, desktop.isPinned(directoryDelegate.filePath))
                                                            selected: directoryDelegate.highlighted
                                                        }
                                                    }
                                                    palette.windowText: highlighted ? root.selectionText : root.fg
                                                    onExpandedChanged: if (expanded) Qt.callLater(() => { if (expanded) desktop.expandDirectory(filePath) })
                                                    Accessible.name: fileName
                                                    Accessible.description: filePath
                                                    // The chevron unfolds (TreeViewDelegate handles it); the name opens, and a folder
                                                    // whose music is all in folders below also unfolds them.
                                                    function open() {
                                                        desktop.openFolder(filePath, 0)
                                                        if (!expanded && desktop.folderNeedsExpand(filePath)) directoryTree.expand(row)
                                                    }
                                                    onPressed: held = false
                                                    onClicked: {
                                                        directoryTree.selectionModel.setCurrentIndex(directoryTree.index(row, 0), ItemSelectionModel.NoUpdate)
                                                        directoryTree.forceActiveFocus()
                                                        if (held) held = false
                                                        else open()
                                                    }
                                                    onPressAndHold: { held = true; root.folderMenuAt(filePath) }
                                                    TapHandler { acceptedButtons: Qt.RightButton; onTapped: root.folderMenuAt(directoryDelegate.filePath) }
                                                }
                                                function currentDelegate() { return currentRow >= 0 ? itemAtCell(Qt.point(0, currentRow)) : null }
                                                Keys.onReturnPressed: if (currentDelegate()) currentDelegate().open()
                                                Keys.onEnterPressed: if (currentDelegate()) currentDelegate().open()
                                                Keys.onMenuPressed: if (currentDelegate()) root.folderMenuAt(currentDelegate().filePath)
                                            }
                                        }
                                    }
                                    AddButton { buttonName: "addFolder"; text: qsTr("Add folder…"); onAdd: folderDialog.open() }
                                }
                            }
                            DropArea {
                                id: folderDrop; objectName: "folderDrop"
                                anchors.fill: parent; keys: ["text/uri-list"]
                                onDropped: drop => {
                                    for (let url of drop.urls) desktop.addFolder(url.toString())
                                    drop.acceptProposedAction()
                                }
                                Rectangle { anchors.fill: parent; color: "transparent"; border.color: root.accent; border.width: 2; visible: folderDrop.containsDrag }
                            }
                        }
                    }
                }
                RowLayout {
                    visible: root.folderView && desktop.folder.total > 250
                    Layout.leftMargin: 6; Layout.rightMargin: 6; spacing: 0
                    FlatButton { icon.source: "icons/chevron.svg"; icon.width: 10; icon.height: 10; icon.color: enabled ? root.muted : root.disabledFg; rotation: 180; implicitWidth: 24; implicitHeight: 24; text: qsTr("Previous page"); enabled: desktop.folder.offset > 0; onClicked: desktop.openFolder(desktop.folder.path, Math.max(0, desktop.folder.offset-250)) }
                    Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; color: root.muted; font.pixelSize: root.fontCaption; text: (desktop.folder.offset + 1) + "–" + Math.min(desktop.folder.offset + 250, desktop.folder.total) + " / " + desktop.folder.total }
                    FlatButton { icon.source: "icons/chevron.svg"; icon.width: 10; icon.height: 10; icon.color: enabled ? root.muted : root.disabledFg; implicitWidth: 24; implicitHeight: 24; text: qsTr("Next page"); enabled: desktop.folder.offset + 250 < desktop.folder.total; onClicked: desktop.openFolder(desktop.folder.path, desktop.folder.offset+250) }
                }
            }
            ColumnLayout {
                SplitView.fillWidth: true; SplitView.minimumWidth: 0
            Rectangle {
                id: player
                // Always full height: before anything plays the record and a slowly drifting
                // waveform already stand where the track will appear.
                // A little taller on tall windows, so the waveform is not squashed flat.
                Layout.fillWidth: true; Layout.preferredHeight: Math.round(Math.max(184, Math.min(300, root.height * .3)))
                Layout.rightMargin: root.edge
                color: root.panel; radius: 6
                RowLayout {
                    anchors.fill: parent; anchors.margins: 10; spacing: 12
                    Cover {
                        objectName: "cover"
                        // Transport controls come first; the artwork shrinks to the room the panel can spare,
                        // down to 72 px, and goes only below a 480 px panel.
                        visible: player.width >= 480
                        idle: !root.loaded
                        readonly property real side: Math.min(player.height - 20, Math.max(72, (player.width - 440) * .8))
                        Layout.preferredWidth: side; Layout.preferredHeight: side; Layout.alignment: Qt.AlignVCenter
                        seed: desktop.audioKey
                        spinning: desktop.playing
                        ready: waveform.ready
                        artwork: desktop.artwork
                        square: desktop.artworkSquare
                        vinyl: root.vinylArtwork
                    }
                ColumnLayout {
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0; spacing: 6
                    ColumnLayout {
                        visible: root.loaded; spacing: 2; Layout.fillWidth: true; Layout.minimumWidth: 0
                        Label {
                            objectName: "nowPlayingTitle"
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            text: desktop.nowPlaying.name || desktop.audio.title || ""
                            textFormat: Text.PlainText; elide: Text.ElideRight; font.pixelSize: root.fontTitle; font.bold: true
                        }
                        // BPM and key sit right after the artist, where the eye already is.
                        RowLayout {
                            id: nowPlayingDetails
                            objectName: "nowPlayingDetails"
                            Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                            visible: !!desktop.nowPlaying.artist || (desktop.nowPlaying.bpm || 0) > 0 || !!desktop.nowPlaying.camelot
                            Label {
                                id: artistLabel
                                // Up to its own width, giving way (elided) before the chips do.
                                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.maximumWidth: implicitWidth
                                visible: text.length > 0; text: desktop.nowPlaying.artist || ""
                                textFormat: Text.PlainText; elide: Text.ElideRight; color: root.silver
                            }
                            Chip { id: bpmChip; objectName: "nowPlayingBpm"; visible: (desktop.nowPlaying.bpm || 0) > 0; text: root.bpmText(desktop.nowPlaying.bpm || 0) + " BPM" }
                            Chip {
                                id: keyChip
                                visible: !!desktop.nowPlaying.camelot
                                code: desktop.nowPlaying.camelot || ""
                                text: (root.keyNotation === "camelot" ? desktop.nowPlaying.camelot : desktop.nowPlaying.classicKey) || ""
                            }
                            Item { Layout.fillWidth: true }
                        }
                    }
                    // A close-up of the 15 s around the playhead, drawn as moving rectangles from the same
                    // per-bin levels, with ticks on the beat grid when the track's BPM and kicks are known.
                    Item {
                        id: zoom; objectName: "zoomStrip"
                        Layout.fillWidth: true; Layout.preferredHeight: 22
                        visible: root.loaded; clip: true
                        readonly property real span: 15
                        readonly property var strip: desktop.waveformColumns(desktop.waveform.length, desktop.waveformKey)
                        readonly property int bins: waveform.ready ? strip.levels.length : 0
                        readonly property real binSeconds: bins > 0 ? waveform.duration / bins : 0
                        readonly property real now: waveform.fraction * waveform.duration
                        readonly property real start: now - span / 2
                        readonly property real period: (desktop.nowPlaying.bpm || 0) > 0 ? 60 / desktop.nowPlaying.bpm : 0
                        // Colours once per waveform, not per frame.
                        readonly property var colours: {
                            let list = [], top = root.waveRestTop
                            for (let i = 0; i < bins; i++)
                                list.push(root.spectral && strip.bands.length === bins ? root.spectralColor(strip.bands[i])
                                          : Qt.tint(root.waveRestFoot, Qt.rgba(top.r, top.g, top.b, strip.levels[i])))
                            return list
                        }
                        Repeater {
                            model: zoom.binSeconds > 0 ? Math.ceil(zoom.span / zoom.binSeconds) + 2 : 0
                            Rectangle {
                                required property int index
                                readonly property int bin: Math.floor(zoom.start / zoom.binSeconds) + index
                                readonly property real level: bin >= 0 && bin < zoom.bins ? zoom.strip.levels[bin] : 0
                                x: (bin * zoom.binSeconds - zoom.start) / zoom.span * zoom.width
                                width: Math.max(1, zoom.binSeconds / zoom.span * zoom.width - 1)
                                height: Math.max(1, level * zoom.height); y: zoom.height - height
                                color: zoom.colours[bin] || "transparent"
                                opacity: (bin + 1) * zoom.binSeconds <= zoom.now ? 1 : root.dark ? .38 : .45
                            }
                        }
                        Repeater {
                            objectName: "beatTicks"
                            model: zoom.period > 0 && root.beatAnchor >= 0 ? Math.ceil(zoom.span / zoom.period) + 1 : 0
                            Rectangle {
                                required property int index
                                readonly property real time: root.beatAnchor + (Math.ceil((zoom.start - root.beatAnchor) / zoom.period) + index) * zoom.period
                                x: (time - zoom.start) / zoom.span * zoom.width; width: 1; height: 5; color: root.muted
                            }
                        }
                        Rectangle { x: zoom.width / 2 - 1; width: 2; height: zoom.height; color: root.fg }
                    }
                    Item {
                        id: waveform; objectName: "waveform"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        property real position: desktop.audio.position || 0
                        readonly property real duration: desktop.audio.duration || 0
                        readonly property int columns: Math.max(1, Math.floor(width / 3))
                        // Levels follow the bar count 50 ms after a resize settles, not every 3 px of a drag.
                        property int levelColumns: columns
                        Timer { id: levelTimer; interval: 50; onTriggered: waveform.levelColumns = waveform.columns }
                        // Levels and low/mid/high shares per bar, read from the waveform the bridge holds.
                        readonly property var columnData: desktop.waveformColumns(levelColumns, desktop.waveformKey)
                        readonly property var levels: columnData.levels
                        readonly property var bands: columnData.bands
                        // The backend sends the waveform for every track, empty when there is none.
                        readonly property bool ready: !!desktop.audioKey && desktop.waveformKey === desktop.audioKey
                        // Until then the bars flicker as noise. A new track scatters the old waveform into the
                        // noise, and the noise settles into the waveform once it lands: blend runs 0 → 1 from
                        // the bars last painted (shown) to the new target.
                        property real wander: 0
                        property real blend: 1
                        property var blendFrom: []
                        property var shown: []
                        // While the bars move (noise, or blending into or out of it) they are drawn by noiseBars,
                        // plain rectangles the GPU batches; the canvas only paints the settled waveform. Repainting
                        // the gradient canvas every frame kept a whole CPU core busy at 60 fps.
                        readonly property bool moving: !ready || blend < 1
                        signal repaint()
                        onLevelsChanged: repaint()
                        // The played and neon canvases are hidden while bars move; once they settle they are
                        // painted again, or they would keep the first frame of the blend (noise over the waveform).
                        onMovingChanged: if (!moving) repaint()
                        onColumnsChanged: { refreshNoise(); levelTimer.restart() }
                        Component.onCompleted: refreshNoise()
                        onReadyChanged: {
                            // Leaving the noise, take the bars from the noise as it stands now: the frame loop
                            // that records them rests while minimized or inactive, and left them stale.
                            if (ready) {
                                let now = []
                                for (let i = 0; i < columns; i++) now.push(level(i, noiseShape, false))
                                shown = now
                            }
                            blendAnimation.stop()
                            blendFrom = shown
                            if (root.motion) {
                                blendAnimation.duration = ready ? 700 : 450
                                blendAnimation.restart()
                            } else blend = 1
                            refreshNoise()
                        }
                        NumberAnimation { id: blendAnimation; target: waveform; property: "blend"; from: 0; to: 1; easing.type: Easing.InOutCubic }
                        // Sets each moving bar's height directly: a binding per bar reading one shared array
                        // copied that array for every bar on every frame.
                        // Heights move in whole device pixels, so a slow drift touches only the bars that moved.
                        function refreshNoise() {
                            let values = [], height = noiseBars.height, pixel = Screen.devicePixelRatio || 1, shape = noiseShape, items = noiseRepeater.bars
                            for (let i = 0; i < columns; i++) {
                                let value = level(i, shape), item = items[i]
                                values.push(value)
                                let cover = Math.round((height - Math.max(1, value * (height - 2))) * pixel) / pixel
                                if (item && item.cover !== cover) item.cover = cover
                            }
                            // What is on screen, for the next track change to scatter from.
                            if (moving) shown = values
                        }
                        // Loading flickers; with nothing loaded the noise rests once the old waveform has
                        // scattered into it (an idle drift every frame cost a fifth of a core at idle).
                        // It moves on every frame the display shows, by the time that frame took, so it glides
                        // instead of stepping (a 15 fps timer stuttered visibly).
                        FrameAnimation {
                            objectName: "noiseAnimation"
                            running: waveform.moving && waveform.visible && root.motion && root.visibility !== Window.Minimized
                                     && (root.loaded || waveform.blend < 1)
                            onTriggered: {
                                if (!waveform.ready) waveform.wander += Math.min(frameTime, .1) * (root.loaded ? 1 : .12)
                                waveform.refreshNoise()
                            }
                        }
                        // Noise, not a wave: every bar has its own random height range, speeds and phases,
                        // unrelated to its neighbours, so the bars rise and fall scattered.
                        function noise(i, k) { let x = Math.sin(i * 12.9898 + k * 78.233) * 43758.5453; return x - Math.floor(x) }
                        // Each bar's speeds, phases and range, worked out once per bar count, not per frame.
                        readonly property var noiseShape: {
                            let shape = []
                            for (let i = 0; i < columns; i++)
                                shape.push([2 + 6 * noise(i, 1), 6.283 * noise(i, 2), 5 + 9 * noise(i, 3), 6.283 * noise(i, 4), .35 + .65 * noise(i, 5)])
                            return shape
                        }
                        function placeholder(i, shape) {
                            let s = (shape || noiseShape)[i]
                            if (!s) return .04
                            let v = .5 + .32 * Math.sin(wander * s[0] + s[1]) + .18 * Math.sin(wander * s[2] + s[3])
                            return Math.max(.04, Math.min(.95, v * s[4]))
                        }
                        function level(i, shape, settled) {
                            let target = (settled === undefined ? ready : settled) ? levels[i] || 0 : placeholder(i, shape)
                            if (blend >= 1 || i >= blendFrom.length) return target
                            return blendFrom[i] + (target - blendFrom[i]) * blend
                        }
                        // The bars are painted once per waveform; progress only moves a clip edge and the cursor, so
                        // position ticks and a drag cost no repaint. While scrubbing, the pointer's time
                        // shows; after release the target shows until the backend confirms it (or 1.5 s pass).
                        property real scrub: -1
                        property real pending: -1
                        // While audio plays with motion on, the playhead follows the interpolated clock of heard audio.
                        readonly property real heard: root.playhead >= 0 ? root.playhead : position
                        readonly property real fraction: scrub >= 0 ? scrub : pending >= 0 ? pending : duration > 0 ? Math.min(1, heard / duration) : 0
                        onPositionChanged: if (pending >= 0 && Math.abs(position / Math.max(1, duration) - pending) < 0.02) pending = -1
                        Timer { id: pendingTimer; interval: 1500; onTriggered: waveform.pending = -1 }
                        function gradient(ctx, height, foot, top) {
                            let gradient = ctx.createLinearGradient(0, height, 0, 0)
                            gradient.addColorStop(0, foot)
                            gradient.addColorStop(1, top)
                            return gradient
                        }
                        function paintBars(ctx, width, height, played, neon) {
                            ctx.reset()
                            // Raspberry at the foot of every bar rising into blue at the top of the panel.
                            ctx.fillStyle = neon ? gradient(ctx, height, root.waveNeonFoot, root.waveNeonTop)
                                                 : gradient(ctx, height, root.waveRestFoot, root.waveRestTop)
                            ctx.globalAlpha = played ? 1 : root.dark ? .38 : .45
                            let count = ready && levels.length ? levels.length : columns, step = width / count, bar = Math.max(1, step - 1)
                            // View → Spectral waveform: each bar in its own low/mid/high mix, brighter for the kick light.
                            let colours = root.spectral && ready && bands.length === count
                            let values = []
                            for (let i = 0; i < count; i++) values.push(level(i))
                            // What is on screen, for the next track change to scatter from.
                            if (!played && !neon && !moving) shown = values
                            for (let i = 0; i < count; i++) {
                                let barHeight = Math.max(1, values[i] * (height - 2))
                                if (colours) ctx.fillStyle = neon ? root.peakColor(root.spectralColor(bands[i])) : root.spectralColor(bands[i])
                                ctx.fillRect(i * step, height - barHeight, bar, barHeight)
                            }
                        }
                        // The unplayed region stays in shadow and never pulses.
                        WaveformBars { objectName: "unplayedBars"; anchors.fill: parent; visible: !waveform.moving }
                        // The same bars while they move: the rest gradient at the unplayed opacity, covered in the
                        // panel colour above each bar and in the one-pixel gap after it.
                        Item {
                            id: noiseBars; objectName: "noiseBars"
                            anchors.fill: parent; clip: true
                            visible: waveform.moving
                            onHeightChanged: waveform.refreshNoise()
                            Rectangle {
                                anchors.fill: parent; opacity: root.dark ? .38 : .45
                                gradient: Gradient {
                                    GradientStop { position: 0; color: root.waveRestTop }
                                    GradientStop { position: 1; color: root.waveRestFoot }
                                }
                            }
                            Repeater {
                                id: noiseRepeater
                                model: noiseBars.visible ? waveform.columns : 0
                                // The bar items by index, so a frame does not ask the Repeater for each one.
                                property var bars: []
                                onItemAdded: (index, item) => { bars[index] = item; Qt.callLater(waveform.refreshNoise) }
                                onItemRemoved: (index, item) => { if (bars[index] === item) bars[index] = null }
                                Item {
                                    required property int index
                                    // Panel height above the bar, set by refreshNoise straight onto the rectangle.
                                    property alias cover: coverRect.height
                                    readonly property real step: noiseBars.width / waveform.columns
                                    readonly property real bar: Math.max(1, step - 1)
                                    x: index * step; width: step; height: noiseBars.height
                                    Rectangle { id: coverRect; width: parent.width; height: parent.height; color: root.panel }
                                    Rectangle { x: parent.bar; width: parent.width - parent.bar; height: parent.height; color: root.panel }
                                }
                            }
                        }
                        Item {
                            clip: true; visible: waveform.ready && !waveform.moving
                            anchors { top: parent.top; bottom: parent.bottom; left: parent.left }
                            width: waveform.fraction * waveform.width
                            WaveformBars { objectName: "playedBars"; width: waveform.width; height: waveform.height; played: true }
                        }
                        // On a kick only the bars just played light up in neon: the last 2 s left of the
                        // cursor, at least 40 px, so the light stays where the sound is.
                        Item {
                            id: neonBand; objectName: "neonBand"
                            clip: true; visible: waveform.ready && !waveform.moving
                            readonly property real span: Math.max(40, 2 / Math.max(1, waveform.duration) * waveform.width)
                            x: Math.max(0, waveform.fraction * waveform.width - span)
                            anchors { top: parent.top; bottom: parent.bottom }
                            width: waveform.fraction * waveform.width - x
                            // Painted once per waveform like the bars under it; the light only fades it in and
                            // out, and the clip above keeps it to the band behind the cursor.
                            WaveformBars {
                                id: neonBars; objectName: "kickBars"
                                x: -neonBand.x; width: waveform.width; height: waveform.height; played: true; neon: true
                                // Drawn by the glow effect when there is one, which fades it itself.
                                opacity: kickGlow.active ? 1 : root.glow
                                visible: !kickGlow.active
                            }
                            // A real glow where the GPU draws it; the software renderer runs no shaders,
                            // so there the neon colour alone carries the kick.
                            Loader {
                                id: kickGlow; objectName: "kickGlow"
                                active: GraphicsInfo.api !== GraphicsInfo.Software && GraphicsInfo.api !== GraphicsInfo.Unknown
                                x: neonBars.x; width: neonBars.width; height: neonBars.height
                                sourceComponent: MultiEffect {
                                    source: neonBars
                                    visible: root.glow > .01; opacity: root.glow
                                    autoPaddingEnabled: true; blurMax: 16
                                    shadowEnabled: true; shadowColor: root.waveNeonTop; shadowBlur: .6
                                    shadowHorizontalOffset: 0; shadowVerticalOffset: 0
                                }
                            }
                        }
                        // Hot cues, numbered, and the estimated drop, over the bars.
                        Repeater {
                            model: 4
                            Rectangle {
                                required property int index
                                readonly property var time: root.trackCues[index]
                                objectName: "cue" + (index + 1)
                                visible: root.loaded && typeof time === "number" && waveform.duration > 0
                                x: (time || 0) / Math.max(1, waveform.duration) * waveform.width; width: 2; height: waveform.height
                                color: root.accent
                                Label { x: 3; text: index + 1; font.pixelSize: root.fontSmall; font.bold: true; color: root.accent }
                            }
                        }
                        Rectangle {
                            objectName: "dropMarker"
                            visible: root.loaded && root.dropTime >= 0 && waveform.duration > 0
                            x: root.dropTime / Math.max(1, waveform.duration) * waveform.width; width: 2; height: waveform.height
                            color: root.warning
                            Label { x: 3; anchors.bottom: parent.bottom; text: qsTr("Drop"); font.pixelSize: root.fontSmall; font.bold: true; color: root.warning }
                        }
                        // Subpixel position: the playhead slides instead of stepping a pixel at a time.
                        Rectangle {
                            objectName: "cursor"; antialiasing: true; visible: root.loaded
                            x: Math.min(waveform.width - 2, waveform.fraction * waveform.width); width: 2; height: parent.height
                            color: Qt.tint(root.fg, Qt.rgba(root.waveNeonTop.r, root.waveNeonTop.g, root.waveNeonTop.b, root.glow))
                        }
                        MouseArea {
                            id: waveformMouse
                            // Nothing to seek in or time to show until a track is loaded.
                            enabled: root.loaded
                            anchors.fill: parent; hoverEnabled: true
                            function fractionAt(x) { return Math.max(0, Math.min(1, x / width)) }
                            onPressed: mouse => waveform.scrub = fractionAt(mouse.x)
                            onPositionChanged: mouse => { if (pressed) waveform.scrub = fractionAt(mouse.x) }
                            onCanceled: waveform.scrub = -1
                            onReleased: mouse => {
                                let target = fractionAt(mouse.x)
                                waveform.scrub = -1
                                root.seekTo(target * waveform.duration)
                            }
                            // The wheel seeks 5 s a notch, forward when turned up; touchpads add up to whole notches.
                            property real wheelRest: 0
                            onWheel: wheel => {
                                wheelRest += wheel.angleDelta.y || -wheel.angleDelta.x
                                while (Math.abs(wheelRest) >= 120) {
                                    let step = wheelRest > 0 ? 1 : -1
                                    wheelRest -= step * 120
                                    root.nudge(5 * step)
                                }
                            }
                            ToolTip.visible: containsMouse; ToolTip.delay: 300
                            ToolTip.text: root.clock(mouseX / width * waveform.duration)
                        }
                        Accessible.role: Accessible.Slider
                        Accessible.name: qsTr("Waveform")
                    }
                    RowLayout {
                        Button { text: qsTr("Previous track"); enabled: root.loaded; display: AbstractButton.IconOnly; implicitWidth: 40; icon.source: "icons/previous.svg"; icon.color: palette.buttonText; icon.width: root.iconLarge; icon.height: root.iconLarge; Accessible.name: text; ToolTip.visible: hovered; ToolTip.delay: root.tipDelay; ToolTip.text: text + " (P)"; onClicked: desktop.step(-1) }
                        Button {
                            id: playButton
                            objectName: "playPause"
                            implicitWidth: 40
                            enabled: root.loaded || root.hasSelection
                            text: root.pauseTarget ? qsTr("Pause") : qsTr("Play")
                            display: AbstractButton.IconOnly
                            icon.source: root.pauseTarget ? "icons/pause.svg" : "icons/play.svg"
                            icon.color: palette.buttonText; icon.width: root.iconLarge; icon.height: root.iconLarge
                            onIconChanged: if (root.motion) playPop.restart()
                            SequentialAnimation {
                                id: playPop
                                NumberAnimation { target: playButton; property: "scale"; to: .82; duration: 70 }
                                NumberAnimation { target: playButton; property: "scale"; to: 1; duration: 180; easing.type: Easing.OutBack }
                            }
                            Accessible.name: text
                            ToolTip.visible: hovered; ToolTip.delay: root.tipDelay; ToolTip.text: text + " (Space)"
                            onClicked: root.playPause()
                        }
                        Button { text: qsTr("Next track"); enabled: root.loaded; display: AbstractButton.IconOnly; implicitWidth: 40; icon.source: "icons/next.svg"; icon.color: palette.buttonText; icon.width: root.iconLarge; icon.height: root.iconLarge; Accessible.name: text; ToolTip.visible: hovered; ToolTip.delay: root.tipDelay; ToolTip.text: text + " (N)"; onClicked: desktop.step(1) }
                        Button { text: qsTr("Stop"); enabled: root.loaded; display: AbstractButton.IconOnly; implicitWidth: 40; icon.source: "icons/stop.svg"; icon.color: palette.buttonText; icon.width: root.iconLarge; icon.height: root.iconLarge; Accessible.name: text; ToolTip.visible: hovered; ToolTip.delay: root.tipDelay; ToolTip.text: text + " (" + stopAction.shortcut + ")"; onClicked: stopAction.trigger() }
                        // A speaker, struck through while muted; the words stay for tooltips and screen readers.
                        ToolButton {
                            objectName: "muteButton"
                            text: root.isMuted ? qsTr("Unmute") : qsTr("Mute")
                            display: AbstractButton.IconOnly
                            icon.source: root.isMuted ? "icons/volume-muted.svg" : "icons/volume.svg"
                            icon.color: palette.buttonText; icon.width: root.iconLarge; icon.height: root.iconLarge
                            implicitWidth: 40
                            Accessible.name: text; ToolTip.visible: hovered; ToolTip.delay: root.tipDelay; ToolTip.text: text + " (M)"
                            onClicked: root.toggleMute()
                        }
                        Slider {
                            id: volumeSlider
                            objectName: "volumeSlider"
                            from: 0; to: 1; value: root.volume
                            // The level fills in the waveform's colour pair, foot to top across the whole track,
                            // so the colour under the knob shows how loud it is; the knob is a small dot.
                            background: Rectangle {
                                x: volumeSlider.leftPadding; y: volumeSlider.topPadding + (volumeSlider.availableHeight - height) / 2
                                width: volumeSlider.availableWidth; height: 4; radius: 2; color: root.alternate
                                Item {
                                    width: volumeSlider.visualPosition * parent.width; height: parent.height; clip: true
                                    Rectangle {
                                        width: volumeSlider.availableWidth; height: parent.height; radius: 2
                                        gradient: Gradient {
                                            orientation: Gradient.Horizontal
                                            GradientStop { position: 0; color: root.waveRestFoot }
                                            GradientStop { position: 1; color: root.waveRestTop }
                                        }
                                    }
                                }
                            }
                            handle: Rectangle {
                                x: volumeSlider.leftPadding + volumeSlider.visualPosition * (volumeSlider.availableWidth - width)
                                y: volumeSlider.topPadding + (volumeSlider.availableHeight - height) / 2
                                width: volumeSlider.pressed || volumeSlider.hovered ? 14 : 12; height: width; radius: width / 2
                                color: root.fg; border.color: root.panel
                            }
                            // A tall track around the thin line, so it is easy to grab.
                            implicitHeight: 32
                            Layout.fillWidth: true; Layout.minimumWidth: 50; Layout.preferredWidth: 110; Layout.maximumWidth: 140
                            onMoved: { root.volume = value; volumeSend.start() }
                            Accessible.name: qsTr("Volume")
                            ToolTip.visible: hovered || pressed; ToolTip.delay: pressed ? 0 : root.tipDelay; ToolTip.text: qsTr("Volume") + " " + Math.round(value * 100) + "%"
                        }
                        Item { visible: root.loaded; Layout.fillWidth: true; Layout.minimumWidth: 0 }
                        Label { visible: !root.loaded; text: root.hasSelection ? qsTr("Press Space to play the selected track") : qsTr("Select a track to play"); color: root.muted; elide: Text.ElideRight; Layout.fillWidth: true; Layout.minimumWidth: 0 }
                        // The time played, or after a click the time left, large; the other one and the length beside it.
                        Label {
                            objectName: "clock"
                            visible: root.loaded
                            readonly property real timeLeft: Math.max(0, (desktop.audio.duration || 0) - (desktop.audio.position || 0))
                            text: root.remainingTime ? "−" + root.clock(timeLeft) : root.clock(desktop.audio.position)
                            font.pixelSize: root.fontDisplay; font.bold: true; font.features: root.tabular; color: root.fg
                            TapHandler { onTapped: root.remainingTime = !root.remainingTime }
                            HoverHandler { id: clockHover; cursorShape: Qt.PointingHandCursor }
                            ToolTip.visible: clockHover.hovered; ToolTip.delay: root.tipDelay
                            ToolTip.text: root.remainingTime ? qsTr("Time left; click to show the time played") : qsTr("Time played; click to show the time left")
                            Accessible.role: Accessible.Button; Accessible.name: ToolTip.text
                        }
                        Label {
                            visible: root.loaded
                            text: (root.remainingTime ? root.clock(desktop.audio.position) : "−" + root.clock(Math.max(0, (desktop.audio.duration || 0) - (desktop.audio.position || 0))))
                                  + " / " + root.clock(desktop.audio.duration)
                            color: root.muted; font.features: root.tabular
                        }
                    }
                }
                }
            }
                // Search and store pills, the status and other filters, then the actions on the selection:
                // one row when the pane has room for all, otherwise search and pills on the first row
                // and the actions with the status filter below them.
                GridLayout {
                    id: toolbar
                    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.rightMargin: root.edge
                    readonly property bool oneRow: width >= 880
                    // Without room for their names the action buttons keep their icons and tooltips only.
                    readonly property bool compact: oneRow ? width < 1400 : width < 640
                    columns: 3; columnSpacing: 10; rowSpacing: 8
                    // Every control of the toolbar is 32 px high with the same faint outline; a filter
                    // that is on takes the selection colour and an accent outline.
                    component Toggle: ToolButton {
                        id: toggle
                        implicitHeight: 32; padding: 6; leftPadding: 10; rightPadding: 10
                        background: Rectangle {
                            radius: 6; border.color: toggle.checked ? root.accent : root.alternate
                            color: toggle.checked ? root.selection : toggle.down ? root.pressedSurface : toggle.hovered ? root.hoverSurface : "transparent"
                        }
                        contentItem: Label {
                            text: toggle.text; textFormat: Text.PlainText; font.features: root.tabular; verticalAlignment: Text.AlignVCenter
                            color: toggle.checked ? root.selectionText : toggle.enabled ? root.fg : root.disabledFg
                        }
                        Accessible.name: text; Accessible.checkable: true; Accessible.checked: checked
                    }
                    RowLayout {
                        id: filterRow
                        Layout.row: 0; Layout.column: 0; Layout.columnSpan: toolbar.oneRow ? 1 : 3
                        Layout.fillWidth: true; Layout.minimumWidth: 0; spacing: 6
                        TextField {
                            id: search; objectName: "trackSearch"; placeholderText: qsTr("Search artist, title, genre, tag or label")
                            Layout.fillWidth: true; Layout.minimumWidth: 140; Layout.preferredWidth: 220; Layout.maximumWidth: 420
                            rightPadding: (slashKey.visible ? slashKey.width : clearSearch.visible ? clearSearch.width : 0) + 18
                            // Framed like the toolbar buttons: a faint outline, the accent only while typing.
                            background: Rectangle {
                                implicitHeight: 32; radius: 6; color: root.bg
                                border.color: search.activeFocus ? root.accent : root.alternate
                            }
                            // The key that jumps here, shown as a key rather than as part of the hint.
                            Rectangle {
                                id: slashKey
                                visible: !search.activeFocus && !search.text
                                anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                                width: 22; height: 22; radius: 4; color: "transparent"; border.color: root.alternate
                                Label { anchors.centerIn: parent; text: "/"; color: root.muted; font.pixelSize: root.fontCaption }
                            }
                            FlatButton {
                                id: clearSearch; objectName: "clearSearch"
                                visible: search.text.length > 0
                                anchors.right: parent.right; anchors.rightMargin: 4; anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Clear search"); icon.source: "icons/close.svg"
                                focusPolicy: Qt.NoFocus
                                onClicked: search.text = ""
                            }
                            onTextChanged: filterTimer.restart()
                            Keys.onReturnPressed: table.forceActiveFocus()
                            Keys.onDownPressed: table.forceActiveFocus()
                            Accessible.name: qsTr("Search tracks")
                        }
                        // One pill per store in the view, several at once; none chosen shows every store.
                        // They scroll sideways when the row is too narrow for all of them.
                        Flickable {
                            id: storePills; objectName: "storePills"
                            visible: !desktop.view.local && desktop.model.stores.length > 0
                            Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: 32
                            Layout.preferredWidth: pillRow.width; Layout.maximumWidth: pillRow.width
                            contentWidth: pillRow.width; contentHeight: 32; clip: true
                            flickableDirection: Flickable.HorizontalFlick; boundsBehavior: Flickable.StopAtBounds
                            Row {
                                id: pillRow; spacing: 6
                                Toggle {
                                    objectName: "storePill-all"
                                    text: qsTr("All stores"); checked: root.storeFilter.length === 0
                                    onClicked: { root.storeFilter = []; filterTimer.restart() }
                                }
                                Repeater {
                                    model: desktop.model.stores
                                    Toggle {
                                        required property var modelData
                                        objectName: "storePill-" + modelData.name
                                        text: modelData.label + " " + modelData.count
                                        checked: root.storeFilter.indexOf(modelData.name) >= 0
                                        onClicked: root.toggleStore(modelData.name)
                                    }
                                }
                            }
                            ScrollIndicator.horizontal: ScrollIndicator {}
                        }
                        // How many tracks the filters leave, in the accent colour while any filter is on.
                        Label {
                            objectName: "filterCount"
                            visible: root.filtersActive && desktop.model.counts.total > 0
                            // Its own room, apart from a pill cut off by the scrolling row, so it never reads as part of it.
                            Layout.minimumWidth: implicitWidth; Layout.leftMargin: 10
                            text: desktop.model.counts.visible + " / " + desktop.model.counts.total
                            color: root.accent; font.features: root.tabular
                            Accessible.name: text; Accessible.description: qsTr("Tracks the filters leave, of all tracks in this view")
                        }
                        Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
                    }
                    RowLayout {
                        Layout.row: toolbar.oneRow ? 0 : 1; Layout.column: 1; spacing: 6
                        ComboBox {
                            id: statusBox; objectName: "statusFilter"
                            // As wide as its longest option in the current language.
                            implicitHeight: 32; implicitContentWidthPolicy: ComboBox.WidestText
                            readonly property var values: ["", "unhandled", "new", "opened", "got", "skip"]
                            model: [qsTr("All statuses"), qsTr("Unhandled"), qsTr("New"), qsTr("Opened"), qsTr("Owned"), qsTr("Skipped")]
                            Binding on currentIndex { value: Math.max(0, statusBox.values.indexOf(root.statusFilter)) }
                            onActivated: index => root.setStatusFilter(values[index])
                            delegate: ItemDelegate {
                                id: statusOption
                                required property int index
                                required property string modelData
                                width: ListView.view.width
                                text: modelData
                                highlighted: statusBox.highlightedIndex === index
                                contentItem: Label {
                                    text: statusOption.text; textFormat: Text.PlainText
                                    color: statusOption.highlighted ? root.selectionText : root.fg
                                }
                                background: Rectangle { color: statusOption.highlighted ? root.selection : root.panel }
                            }
                            ToolTip.visible: hovered; ToolTip.delay: root.tipDelay; ToolTip.text: qsTr("Show tracks by status; H switches between all and unhandled")
                            Accessible.name: qsTr("Status filter")
                        }
                        Toggle {
                            id: moreFilters; objectName: "moreFilters"
                            text: qsTr("Filters"); checked: root.extraFilters
                            onClicked: filterPopup.open()
                            ToolTip.visible: hovered; ToolTip.delay: root.tipDelay; ToolTip.text: desktop.view.local ? qsTr("BPM, key and genre") : qsTr("Genre")
                            Popup {
                                id: filterPopup
                                y: parent.height + 4; x: parent.width - width
                                padding: 12
                                background: Rectangle { color: root.panel; border.color: root.alternate; radius: 4 }
                                contentItem: GridLayout {
                                    columns: 2; columnSpacing: 10; rowSpacing: 8
                                    Label { text: qsTr("BPM"); visible: !!desktop.view.local }
                                    RowLayout {
                                        visible: !!desktop.view.local; spacing: 6
                                        TextField {
                                            objectName: "bpmMin"; implicitWidth: 64; placeholderText: qsTr("min")
                                            validator: IntValidator { bottom: 0; top: 999 }
                                            text: root.bpmMin ? String(root.bpmMin) : ""
                                            onTextEdited: { root.bpmMin = Number(text) || 0; filterTimer.restart() }
                                            Accessible.name: qsTr("Lowest BPM")
                                        }
                                        Label { text: "–" }
                                        TextField {
                                            objectName: "bpmMax"; implicitWidth: 64; placeholderText: qsTr("max")
                                            validator: IntValidator { bottom: 0; top: 999 }
                                            text: root.bpmMax ? String(root.bpmMax) : ""
                                            onTextEdited: { root.bpmMax = Number(text) || 0; filterTimer.restart() }
                                            Accessible.name: qsTr("Highest BPM")
                                        }
                                    }
                                    Label { text: qsTr("Key"); visible: !!desktop.view.local }
                                    CheckBox {
                                        visible: !!desktop.view.local
                                        text: qsTr("Mixes with the playing track")
                                        enabled: !!desktop.nowPlaying.camelot || checked
                                        checked: root.keyMatch
                                        onToggled: { root.keyMatch = checked; filterTimer.restart() }
                                    }
                                    Label { text: qsTr("Genre") }
                                    TextField {
                                        objectName: "genreFilter"; Layout.preferredWidth: 180; placeholderText: qsTr("any")
                                        text: root.genreFilter
                                        onTextEdited: { root.genreFilter = text; filterTimer.restart() }
                                        Accessible.name: qsTr("Genre")
                                    }
                                    Button {
                                        Layout.columnSpan: 2; Layout.alignment: Qt.AlignRight
                                        text: qsTr("Clear filters"); enabled: root.filtersActive
                                        onClicked: root.resetFilters(true)
                                    }
                                }
                            }
                        }
                    }
                    // The actions on the selected tracks, with how many are selected.
                    RowLayout {
                        id: trackActions
                        objectName: "trackActions"
                        // Present as soon as a view is loaded; entries that need a selection are disabled without one.
                        // Never narrower than its buttons: the filters give way first.
                        Layout.row: toolbar.oneRow ? 0 : 1; Layout.column: toolbar.oneRow ? 2 : 0
                        Layout.fillWidth: !toolbar.oneRow
                        visible: !!desktop.view.title
                        spacing: 6
                        // Each button is its own outlined tile with room around it, so the row never
                        // reads as one run of text.
                        component ActionButton: FlatButton {
                            id: actionButton
                            display: toolbar.compact ? AbstractButton.IconOnly : AbstractButton.TextBesideIcon
                            icon.width: root.iconSmall; icon.height: root.iconSmall
                            implicitHeight: 32; padding: 6; leftPadding: 10; rightPadding: 10; spacing: 6
                            // Only a faint outline at rest; the surface appears on hover.
                            background: Rectangle {
                                radius: 6; border.color: root.alternate
                                color: actionButton.down ? root.pressedSurface : actionButton.hovered ? root.hoverSurface : "transparent"
                            }
                        }
                        Label {
                            objectName: "selectionCount"
                            visible: root.hasSelection
                            text: qsTr("%n selected", "", desktop.model.counts.selected)
                            color: root.muted; font.features: root.tabular
                        }
                        // The same actions as the shortcuts, so a tooltip always names the key that works.
                        // Playlist actions show in playlist views, file actions in local views.
                        ActionButton { objectName: "openButton"; action: openAction; icon.source: "icons/link.svg"; visible: !desktop.view.local }
                        ActionButton { action: downloadAction; icon.source: "icons/download.svg"; visible: !desktop.view.local }
                        ActionButton { action: ownedAction; icon.source: "icons/owned.svg"; visible: !desktop.view.local }
                        ActionButton { objectName: "skipButton"; action: skipAction; icon.source: "icons/skip.svg" }
                        ActionButton { action: analyzeAction; icon.source: "icons/analyze.svg"; visible: !!desktop.view.local }
                        ActionButton {
                            id: moreActions
                            text: qsTr("More actions"); icon.source: "icons/more.svg"; display: AbstractButton.IconOnly
                            onClicked: contextMenu.popup(moreActions, 0, moreActions.height)
                        }
                    }
                }
                Timer { id: filterTimer; interval: 120; onTriggered: root.applyFilters() }
                HorizontalHeaderView {
                    id: header; syncView: table; Layout.fillWidth: true; Layout.rightMargin: root.edge; clip: true
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
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.rightMargin: root.edge
                // Below the last row a right click keeps the selection and opens the same menu, with Select all.
                MouseArea {
                    objectName: "tableBackground"
                    anchors.fill: parent; acceptedButtons: Qt.RightButton; enabled: !!desktop.view.title
                    onClicked: { table.forceActiveFocus(); contextMenu.popup() }
                }
                TableView {
                    id: table; objectName: "trackTable"
                    // The model keeps the keyboard row as a track key, so a sort or filter never moves it to another track.
                    readonly property int keyboardRow: desktop.model.cursorRow
                    anchors.fill: parent
                    rowHeightProvider: () => root.rowHeight
                    property int rowHeight: root.rowHeight
                    onRowHeightChanged: forceLayout()
                    clip: true; model: desktop.model; reuseItems: true
                    // Title takes the remaining width so status and stores stay on screen.
                    // BPM and key only mean something for local files; SoundCloud rows never carry them.
                    // A width the user dragged or fitted wins; without one the saved width applies.
                    function chosenWidth(column) {
                        let explicit = explicitColumnWidth(column)
                        // Chips, statuses and numbers need room even when an older, narrower width was saved.
                        return Math.max(root.minimumColumnWidths[column], explicit >= 0 ? explicit : root.columnWidths[column])
                    }
                    columnWidthProvider: function(column) {
                        if (!root.columnVisible(column)) return 0
                        if (column !== root.titleColumn || explicitColumnWidth(column) >= 0) return chosenWidth(column)
                        let used = 0
                        for (let i of root.allColumns) if (i !== root.titleColumn && root.columnVisible(i)) used += chosenWidth(i)
                        // Up to the right edge, like the search field above; the scroll bar floats over the stores.
                        return Math.max(150, width - used)
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
                        required property string stores
                        required property int row
                        required property int column
                        // The loaded track, playing or paused.
                        readonly property bool playing: desktop.audioKey === trackKey
                        readonly property bool titleCell: column === root.titleColumn
                        implicitHeight: root.rowHeight; implicitWidth: 100
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
                        // The loaded track's row: an accent tint and a bar at its left edge, both quieter while paused.
                        Rectangle {
                            visible: cell.playing; anchors.fill: parent; color: root.accent
                            // Light: kept faint so it never reads as the selection colour.
                            opacity: desktop.playing ? (root.dark ? .22 : .09) : (root.dark ? .08 : .04)
                        }
                        Rectangle {
                            objectName: "nowPlayingBar"
                            visible: cell.playing && cell.column === root.firstVisibleColumn
                            width: 3; height: parent.height; color: desktop.playing ? root.accent : root.muted
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
                        // One outline around the whole keyboard row: each cell draws its top and bottom edge,
                        // and only the outermost visible columns draw the sides.
                        Item {
                            visible: row === table.keyboardRow && table.activeFocus; anchors.fill: parent
                            Rectangle { width: parent.width; height: 1; color: root.accent }
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.accent }
                            Rectangle { visible: cell.column === root.firstVisibleColumn; width: 1; height: parent.height; color: root.accent }
                            Rectangle { visible: cell.column === root.lastVisibleColumn; anchors.right: parent.right; width: 1; height: parent.height; color: root.accent }
                        }
                        // In a playlist, a folder before the name says the track is on this computer.
                        readonly property bool found: cell.titleCell && cell.local && !desktop.view.local
                        // The name keeps a fixed slot for the play state, so it never shifts when a track starts or stops.
                        ColorImage {
                            objectName: "playState"
                            visible: cell.titleCell && cell.playing
                            x: 7; anchors.verticalCenter: parent.verticalCenter
                            width: 12; height: 12; sourceSize: Qt.size(12, 12)
                            source: desktop.playing ? "icons/play.svg" : "icons/pause.svg"
                            color: cell.chosen ? root.selectionText : desktop.playing ? root.accent : root.muted
                        }
                        ColorImage {
                            objectName: "foundLocally"
                            visible: cell.found
                            x: 25; anchors.verticalCenter: parent.verticalCenter
                            width: 14; height: 14; sourceSize: Qt.size(14, 14); source: "icons/folder.svg"
                            color: cell.chosen ? root.selectionText : root.silver
                            HoverHandler { id: foundHover }
                            ToolTip.visible: foundHover.hovered; ToolTip.delay: root.tipDelay; ToolTip.text: qsTr("Found in your local files")
                        }
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
                            anchors.fill: parent; anchors.rightMargin: 7; anchors.leftMargin: cell.titleCell ? (cell.found ? 45 : 25) : 7
                            verticalAlignment: Text.AlignVCenter; textFormat: Text.PlainText; elide: Text.ElideRight
                            horizontalAlignment: [4, 6, 8].indexOf(cell.column) >= 0 ? Text.AlignRight : Text.AlignLeft
                            font.features: root.tabular
                            text: cell.display
                            color: cell.chosen ? root.selectionText : cell.progress >= 0 ? root.fg : cell.column === 0 ? root.statusColor(cell.status)
                                 : [3, 7, 8, 10, 11].indexOf(cell.column) >= 0 ? root.muted : root.fg
                        }
                        Row {
                            visible: cell.column === 9
                            anchors.fill: parent; anchors.margins: root.compactRows ? 3 : 6; spacing: 4; clip: true
                            Repeater {
                                // "no-link" is the absence of a store, so it leaves the cell empty.
                                // A gate badge names its site; its category (same position) picks the colour.
                                model: cell.column === 9 ? cell.display.split(", ").map((name, i) => ({ name: name, category: cell.stores.split(", ")[i] }))
                                                           .filter(s => s.name && s.category !== "no-link") : []
                                Rectangle {
                                    required property var modelData
                                    width: badge.implicitWidth + 10; height: root.compactRows ? 20 : 22; radius: 4
                                    color: cell.chosen ? root.panel : root.alternate
                                    Label { id: badge; anchors.centerIn: parent; text: parent.modelData.name; font.pixelSize: root.fontCaption; color: root.storeColor(parent.modelData.category) }
                                }
                            }
                        }
                        MouseArea {
                            anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.RightButton
                            // Dragging across rows selects them, so the view never takes the press over to flick.
                            preventStealing: true
                            property int dragRow: -1
                            // A plain press on a selected row waits for the release: it may be a slow second
                            // click that renames, or, in local views, the start of a drag of the selection onto
                            // a local playlist in the sidebar. A drag from an unselected row selects rows instead.
                            property bool deferred: false
                            property point pressedAt
                            onPressed: function(mouse) {
                                // Taking focus first saves an open in-place edit (its editor saves on focus loss)
                                // before the selection or the menu change anything.
                                table.forceActiveFocus()
                                dragRow = row
                                pressedAt = Qt.point(mouse.x, mouse.y)
                                let toggle = (mouse.modifiers & Qt.ControlModifier) !== 0, extend = (mouse.modifiers & Qt.ShiftModifier) !== 0
                                deferred = mouse.button === Qt.LeftButton && chosen && !toggle && !extend
                                if (mouse.button === Qt.RightButton ? !chosen : !deferred) desktop.model.select(row, toggle, extend)
                            }
                            onPositionChanged: function(mouse) {
                                if (!(mouse.buttons & Qt.LeftButton) || dragRow < 0) return
                                if (trackDrag.Drag.active) { trackDrag.follow(mapToItem(null, mouse.x, mouse.y)); return }
                                if (deferred && desktop.view.local
                                        && Math.hypot(mouse.x - pressedAt.x, mouse.y - pressedAt.y) > Application.styleHints.startDragDistance) {
                                    deferred = false
                                    trackDrag.start(mapToItem(null, mouse.x, mouse.y))
                                    return
                                }
                                // The row under the pointer; past the top or bottom edge the view scrolls a row per move.
                                let y = mapToItem(table.contentItem, mouse.x, mouse.y).y
                                let top = table.contentY, bottom = table.contentY + table.height - 1
                                let target = table.cellAtPosition(Qt.point(cell.x + 1, Math.max(top, Math.min(bottom, y))), true).y
                                if (target < 0) return
                                if (y < top) target = Math.max(0, target - 1)
                                else if (y > bottom) target = Math.min(table.rows - 1, target + 1)
                                if (target === dragRow) return
                                if (deferred) { deferred = false; desktop.model.select(dragRow, false, false) }
                                dragRow = target
                                desktop.model.select(target, (mouse.modifiers & Qt.ControlModifier) !== 0, true)
                                table.positionViewAtRow(target, TableView.Contain)
                            }
                            onReleased: {
                                dragRow = -1
                                if (trackDrag.Drag.active) { trackDrag.Drag.drop(); return }
                                if (!deferred) return
                                deferred = false
                                let only = desktop.model.counts.selected === 1
                                desktop.model.select(row, false, false)
                                if (only && cell.editable) { slowClick.key = cell.trackKey; slowClick.column = cell.column; slowClick.restart() }
                            }
                            onCanceled: { dragRow = -1; deferred = false; trackDrag.Drag.cancel() }
                            onClicked: function(mouse) { if (mouse.button === Qt.RightButton) contextMenu.popup() }
                            // A double click always plays; renaming is F2 or a slow second click on a selected name.
                            onDoubleClicked: function(mouse) {
                                if (mouse.button !== Qt.LeftButton) return
                                deferred = false
                                slowClick.stop()
                                desktop.action("play")
                            }
                        }
                        // In folder views the artist and the file's name are edited in place;
                        // a new name renames the file on disk.
                        readonly property bool editable: !!desktop.view.local && (cell.column === 1 || cell.column === root.titleColumn)
                        Loader {
                            active: cell.editable && table.editKey === cell.trackKey && table.editColumn === cell.column
                            anchors.fill: parent; anchors.margins: 2; z: 2
                            sourceComponent: TextField {
                                objectName: "cellEditor"
                                text: cell.display; selectByMouse: true
                                leftPadding: 5; rightPadding: 5; topPadding: 0; bottomPadding: 0
                                background: Rectangle { color: root.panel; border.color: root.accent; border.width: 2; radius: 3 }
                                property bool done: false
                                function finish(save) {
                                    if (done) return
                                    done = true
                                    if (save && text.trim() !== cell.display)
                                        desktop.rename(cell.trackKey, cell.column === root.titleColumn ? "title" : "artist", text)
                                    Qt.callLater(table.stopEditing)
                                }
                                Component.onCompleted: { forceActiveFocus(); selectAll() }
                                // Handled here: the field would let Enter on to the table, where it opens links.
                                Keys.onReturnPressed: event => { finish(true); event.accepted = true }
                                Keys.onEnterPressed: event => { finish(true); event.accepted = true }
                                onActiveFocusChanged: if (!activeFocus) finish(true)
                                Keys.onEscapePressed: event => { finish(false); event.accepted = true }
                                // Every key belongs to the editor: no single-key shortcut fires while typing.
                                Keys.onShortcutOverride: event => event.accepted = true
                                Keys.onUpPressed: event => event.accepted = true
                                Keys.onDownPressed: event => event.accepted = true
                                Accessible.name: cell.column === root.titleColumn ? qsTr("Track name") : qsTr("Artist")
                            }
                        }
                    }
                    property string editKey: ""
                    property int editColumn: -1
                    function startEditing(key, column) { editKey = key; editColumn = column }
                    // A menu opened by the click that ended the edit keeps the keyboard; focus comes back when it closes.
                    function stopEditing() { editKey = ""; editColumn = -1; if (!contextMenu.visible) forceActiveFocus() }
                    function popupMenu() {
                        if (keyboardRow >= 0) positionViewAtRow(keyboardRow, TableView.Contain)
                        let item = keyboardRow >= 0 ? itemAtCell(Qt.point(root.titleColumn, keyboardRow)) : null
                        if (item) contextMenu.popup(item, 0, item.height); else contextMenu.popup(table, 0, 0)
                    }
                    // A second click on the only selected name renames it once a double click is ruled out.
                    Timer {
                        id: slowClick
                        property string key: ""
                        property int column: -1
                        interval: Qt.styleHints.mouseDoubleClickInterval + 100
                        onTriggered: if (desktop.model.counts.selected === 1 && desktop.model.firstSelectedKey === key) table.startEditing(key, column)
                    }
                    // Shift extends from the anchor; Ctrl+Shift adds the range to the selection.
                    // Without a keyboard row, moving down starts at the first row and moving up at the last.
                    function moveCursor(delta, event) {
                        if (rows === 0) return
                        let extend = (event.modifiers & Qt.ShiftModifier) !== 0, add = extend && (event.modifiers & Qt.ControlModifier) !== 0
                        let target = Math.max(0, Math.min(rows - 1, (keyboardRow >= 0 ? keyboardRow : delta > 0 ? -1 : rows) + delta))
                        desktop.model.select(target, add, extend)
                        positionViewAtRow(target, TableView.Contain)
                    }
                    readonly property int pageRows: Math.max(1, Math.floor(height / root.rowHeight) - 1)
                    Keys.onUpPressed: event => moveCursor(-1, event)
                    Keys.onDownPressed: event => moveCursor(1, event)
                    Keys.onPressed: function(event) {
                        if (event.matches(StandardKey.Copy)) { root.copied(desktop.copy()); event.accepted = true }
                        else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { if (root.hasSelection) desktop.action("play"); event.accepted = true }
                        else if (event.key === Qt.Key_F2) {
                            if (desktop.view.local && desktop.model.counts.selected === 1) {
                                if (keyboardRow >= 0) positionViewAtRow(keyboardRow, TableView.Contain)
                                startEditing(desktop.model.firstSelectedKey, root.titleColumn)
                            }
                            event.accepted = true
                        }
                        else if (event.key === Qt.Key_Menu || (event.key === Qt.Key_F10 && (event.modifiers & Qt.ShiftModifier))) { popupMenu(); event.accepted = true }
                        else if (event.key === Qt.Key_Delete) { if (root.hasSelection && root.remoteView) desktop.action("remove"); event.accepted = true }
                        else if (event.key === Qt.Key_Home) { moveCursor(-rows, event); event.accepted = true }
                        else if (event.key === Qt.Key_End) { moveCursor(rows, event); event.accepted = true }
                        else if (event.key === Qt.Key_PageUp) { moveCursor(-pageRows, event); event.accepted = true }
                        else if (event.key === Qt.Key_PageDown) { moveCursor(pageRows, event); event.accepted = true }
                    }
                    ScrollBar.vertical: ScrollBar {}
                    ScrollBar.horizontal: ScrollBar {}
                    Accessible.name: qsTr("Tracks")
                }
                ColumnLayout {
                    id: emptyState; objectName: "emptyState"
                    anchors.centerIn: parent; width: Math.min(parent.width - 40, 520); spacing: 12
                    visible: !root.hasRows && desktop.ready
                    // Nothing open yet: what to do first, as buttons, with the keys underneath.
                    readonly property bool start: !desktop.view.title
                    ColorImage {
                        visible: emptyState.start; Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: 40; Layout.preferredHeight: 40; sourceSize: Qt.size(40, 40)
                        source: "icons/playlist.svg"; color: root.muted
                    }
                    Label {
                        visible: emptyState.start; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
                        text: qsTr("Start digging"); font.pixelSize: root.fontTitle; font.bold: true; color: root.fg
                        Accessible.role: Accessible.Heading
                    }
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; color: root.muted
                        text: desktop.model.counts.total > 0 ? qsTr("No tracks match the search or filters. Press Esc to clear them.")
                            : desktop.view.title ? qsTr("This view has no tracks.")
                            : qsTr("Pick a playlist or folder in the sidebar, or add one.")
                    }
                    RowLayout {
                        visible: emptyState.start; Layout.alignment: Qt.AlignHCenter; spacing: 8
                        Button { objectName: "emptyAddPlaylist"; action: addPlaylistAction }
                        Button { objectName: "emptyAddFolder"; action: addFolderAction }
                        Button { objectName: "emptyImportProfile"; text: qsTr("Import profile playlists…"); enabled: !desktop.busy; onClicked: desktop.action("profile") }
                    }
                    Label {
                        visible: emptyState.start; Layout.fillWidth: true; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                        text: qsTr("Shortcuts: A adds a playlist, Ctrl+O a folder; F1 lists every key.")
                        color: root.muted; font.pixelSize: root.fontCaption
                    }
                    Button {
                        objectName: "clearFilters"; Layout.alignment: Qt.AlignHCenter
                        visible: desktop.model.counts.total > 0 && root.filtersActive
                        text: qsTr("Clear filters"); onClicked: { root.resetFilters(true); table.forceActiveFocus() }
                    }
                }
                }
                Rectangle {
                    id: errorBanner; objectName: "errorBanner"
                    visible: root.errorMessage.length > 0
                    Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.rightMargin: root.edge
                    implicitHeight: errorContent.implicitHeight + 16
                    color: root.panel; border.color: root.danger; radius: 4
                    // Screen readers announce it as an alert when it appears.
                    Accessible.role: Accessible.AlertMessage
                    Accessible.name: root.errorMessage
                    RowLayout {
                        id: errorContent
                        anchors.fill: parent; anchors.margins: 8
                        Label {
                            id: errorText
                            Layout.fillWidth: true; Layout.minimumWidth: 0
                            text: root.errorMessage; textFormat: Text.PlainText
                            wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                            color: root.danger; Accessible.name: text
                        }
                        // Three lines at most: a longer message ends in "…" and its button says the rest is there.
                        Button { objectName: "errorDetails"; text: errorText.truncated ? qsTr("Show full error") : qsTr("Details"); onClicked: messageLog.open() }
                        FlatButton { objectName: "dismissError"; text: qsTr("Dismiss error"); icon.source: "icons/close.svg"; onClicked: root.errorMessage = "" }
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
                    objectName: "statusCounts"
                    color: root.muted; elide: Text.ElideRight; font.features: root.tabular
                    property var c: desktop.model.counts
                    text: root.hasRows || c.total > 0 ? qsTr("%1 / %2 tracks · owned %3 · skipped %4").arg(c.visible).arg(c.total).arg(c.got).arg(c.skipped) : ""
                    Accessible.name: text
                    Accessible.description: qsTr("Tracks shown of all tracks in this view, and how many are owned or skipped")
                }
                BusyIndicator {
                    running: desktop.busy || root.shuttingDown || !desktop.ready; visible: running; implicitHeight: 22; implicitWidth: 22
                    Accessible.name: qsTr("Working")
                }
                // An import shows its stage and how many of how many tracks are in.
                ProgressBar {
                    objectName: "importProgress"
                    visible: (desktop.importProgress.total || 0) > 0
                    from: 0; to: Math.max(1, desktop.importProgress.total || 0); value: desktop.importProgress.done || 0
                    Layout.preferredWidth: 160; Layout.minimumWidth: 60
                    Behavior on value { enabled: root.motion; NumberAnimation { duration: 150 } }
                    Accessible.name: qsTr("Import progress")
                    Accessible.description: qsTr("%1 of %2").arg(desktop.importProgress.done || 0).arg(desktop.importProgress.total || 0)
                }
                FlatButton { text: qsTr("Cancel"); display: AbstractButton.TextOnly; visible: desktop.busy; implicitHeight: 24; onClicked: desktop.action("cancel") }
                Label {
                    objectName: "statusMessage"
                    Layout.fillWidth: true; Layout.minimumWidth: 0; textFormat: Text.PlainText; elide: Text.ElideRight
                    readonly property var importing: desktop.importProgress
                    text: root.shuttingDown ? qsTr("Closing…") : !desktop.ready ? qsTr("Loading library…")
                        : importing.stage ? importing.stage + (importing.total ? " " + importing.done + " / " + importing.total : "")
                        : desktop.level === "error" ? (root.errorMessage ? qsTr("Error — see the message above") : "") : desktop.message
                    font.features: root.tabular
                    color: desktop.level === "error" ? root.danger : root.muted
                    font.bold: desktop.level === "error"
                    ToolTip.visible: hovered && text.length > 0; ToolTip.delay: root.tipDelay; ToolTip.text: text
                    HoverHandler { id: messageHover }
                    property bool hovered: messageHover.hovered
                    TapHandler { onTapped: messageLog.open() }
                    Accessible.name: text
                }
            }
        }
    }
    FolderDialog { id: folderDialog; title: qsTr("Add folder"); onAccepted: desktop.addFolder(selectedFolder.toString()) }
    // One keyboard focus ring for every control: 2 px in the accent colour inside whichever control
    // Tab, an arrow move or a shortcut gave focus to; a click never shows it (visualFocus).
    Rectangle {
        id: focusRing; objectName: "focusRing"
        readonly property Item target: root.activeFocusItem
        readonly property bool shown: !!target && target.visualFocus === true && target.focusRing !== false
        parent: shown ? target : root.contentItem
        visible: shown
        anchors.fill: parent; z: 1000
        color: "transparent"; border.width: 2; border.color: root.accent
        radius: shown && target.background && typeof target.background.radius === "number" ? target.background.radius : 0
    }
    // Short confirmations (copied, added, downloaded, imported…) in the bottom-right corner: three at
    // most, each for 3 s, newest at the bottom; a click dismisses one. They never take focus.
    ListModel { id: toastModel }
    function toast(text) {
        if (!text || shuttingDown) return
        toastModel.append({text: String(text)})
        while (toastModel.count > 3) toastModel.remove(0)
    }
    function copied(count) { if (count > 0) toast(qsTr("Copied to the clipboard")) }
    Popup {
        id: toasts; objectName: "toasts"
        parent: Overlay.overlay
        // Above the status bar and the error banner, clear of the window edge.
        x: parent.width - width - 16; y: parent.height - height - 36 - (errorBanner.visible ? errorBanner.height + 8 : 0)
        width: Math.min(360, root.width - 32); height: toastList.contentHeight
        padding: 0; modal: false; dim: false; focus: false
        closePolicy: Popup.NoAutoClose
        background: null
        visible: toastModel.count > 0
        contentItem: ListView {
            id: toastList; objectName: "toastList"
            interactive: false; spacing: 6; model: toastModel
            add: Transition { enabled: root.motion; NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } }
            displaced: Transition { enabled: root.motion; NumberAnimation { property: "y"; duration: 150 } }
            delegate: Rectangle {
                id: toastItem
                required property int index
                required property string text
                width: ListView.view.width; height: toastLabel.implicitHeight + 20
                radius: 6; color: root.panel; border.color: root.border
                Label {
                    id: toastLabel
                    anchors.fill: parent; anchors.margins: 10; verticalAlignment: Text.AlignVCenter
                    text: toastItem.text; textFormat: Text.PlainText; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight
                    color: root.fg
                }
                Timer { running: true; interval: 3000; onTriggered: toastModel.remove(toastItem.index) }
                TapHandler { onTapped: toastModel.remove(toastItem.index) }
                Accessible.role: Accessible.AlertMessage
                Accessible.name: text
            }
        }
    }
    // Follows the pointer while selected tracks are dragged from the table.
    Rectangle {
        id: trackDrag; objectName: "trackDrag"
        property point at
        function follow(scenePoint) { at = root.contentItem.mapFromItem(null, scenePoint) }
        function start(scenePoint) { follow(scenePoint); Drag.active = true }
        x: at.x + 12; y: at.y + 12; z: 100
        visible: Drag.active
        width: dragLabel.implicitWidth + 16; height: 24; radius: 4
        color: root.panel; border.color: root.accent
        Drag.keys: [root.trackDragKey]
        Drag.hotSpot: Qt.point(-12, -12)
        Label { id: dragLabel; anchors.centerIn: parent; text: desktop.model.counts.selected === 1 ? qsTr("1 track") : qsTr("%n tracks", "", desktop.model.counts.selected); color: root.fg; font.pixelSize: root.fontCaption }
    }
    // The sidebar menus act on the row they were opened on, not on the view that is loaded.
    function folderMenuAt(path) { folderMenu.path = path; folderMenu.pinned = desktop.isPinned(path); folderMenu.popup() }
    AppMenu {
        id: folderMenu
        objectName: "folderMenu"
        property string path
        property bool pinned: false
        AppMenuItem { text: qsTr("Open"); onTriggered: desktop.openFolder(folderMenu.path, 0) }
        AppMenuItem { text: qsTr("Analyze folder"); enabled: !desktop.busy; onTriggered: desktop.folderAction("analyze_folder", folderMenu.path) }
        AppMenuItem {
            text: qsTr("Rescan")
            onTriggered: {
                desktop.rescanFolder(folderMenu.path)
                if (root.folderView && desktop.samePath(desktop.folder.path, folderMenu.path)) desktop.openFolder(desktop.folder.path, desktop.folder.offset)
            }
        }
        AppSeparator {}
        AppMenuItem { text: qsTr("Show in file manager"); onTriggered: desktop.showFolder(folderMenu.path) }
        AppMenuItem { text: qsTr("Copy path"); onTriggered: root.copied(desktop.copyText(folderMenu.path)) }
        AppSeparator {}
        // A pinned folder is a root of its own; unpinning takes it off the list.
        AppMenuItem { text: qsTr("Pin folder"); visible: !folderMenu.pinned; onTriggered: desktop.folderAction("pin", folderMenu.path) }
        AppMenuItem { text: qsTr("Unpin folder"); visible: folderMenu.pinned; onTriggered: desktop.folderAction("unpin", folderMenu.path) }
        // Brought back from the Folders menu, Hidden folders.
        AppMenuItem { text: qsTr("Hide folder"); onTriggered: desktop.folderAction("hide_folder", folderMenu.path) }
    }
    function playlistMenuAt(source) {
        playlistMenu.source = source
        playlistMenu.local = source.startsWith("local-playlist:")
        playlistMenu.pending = desktop.playlists.some(p => p.source === source && p.pending)
        playlistMenu.popup()
    }
    AppMenu {
        id: playlistMenu
        objectName: "playlistMenu"
        property string source
        property bool local: false
        property bool pending: false
        AppMenuItem { text: qsTr("Open"); enabled: !playlistMenu.pending; onTriggered: desktop.load(playlistMenu.source) }
        // SoundCloud playlists import again from SoundCloud; local ones have nothing to fetch.
        AppMenuItem { text: qsTr("Refresh"); visible: !playlistMenu.local; enabled: !desktop.busy && !playlistMenu.pending; onTriggered: desktop.playlistAction("refresh", playlistMenu.source) }
        AppMenuItem { text: qsTr("Rename…"); enabled: !playlistMenu.pending; onTriggered: desktop.playlistAction("rename_playlist", playlistMenu.source) }
        AppMenuItem { text: qsTr("Duplicate…"); visible: playlistMenu.local; onTriggered: desktop.playlistAction("duplicate_playlist", playlistMenu.source) }
        // Every file of a local playlist, without opening it and selecting them all.
        AppMenuItem {
            text: qsTr("Export audio…"); visible: playlistMenu.local; enabled: !desktop.busy
            reason: desktop.busy ? qsTr("wait for the current task") : ""
            onTriggered: desktop.playlistAction("export", playlistMenu.source)
        }
        AppSeparator {}
        AppMenuItem { text: qsTr("Delete playlist…"); enabled: !playlistMenu.pending; onTriggered: desktop.deletePlaylist(playlistMenu.source) }
    }
    AppMenu {
        id: playlistsSectionMenu
        AppMenuItem { text: qsTr("Add playlist…"); onTriggered: desktop.action("dig") }
        AppMenuItem { text: qsTr("Import profile playlists…"); onTriggered: desktop.action("profile") }
    }
    AppMenu {
        id: foldersSectionMenu
        objectName: "foldersSectionMenu"
        AppMenuItem { text: qsTr("Add folder…"); onTriggered: folderDialog.open() }
        AppSeparator {}
        // Hidden folders are listed here; choosing one shows it in the tree again.
        AppMenu {
            id: hiddenFoldersMenu
            objectName: "hiddenFoldersMenu"
            title: qsTr("Hidden folders")
            AppMenuItem { text: qsTr("None"); enabled: false; visible: desktop.hiddenFolders.length === 0 }
            Instantiator {
                model: desktop.hiddenFolders
                delegate: AppMenuItem {
                    required property string modelData
                    text: modelData
                    onTriggered: desktop.folderAction("unhide_folder", modelData)
                }
                onObjectAdded: (index, object) => hiddenFoldersMenu.insertItem(index + 1, object)
                onObjectRemoved: (index, object) => hiddenFoldersMenu.removeItem(object)
            }
        }
    }
    TextMetrics { id: fitMetrics }
    TextMetrics { id: badgeMetrics; font.pixelSize: root.fontCaption }
    AppMenu {
        id: columnMenu
        property int column: 0
        AppMenuItem { text: qsTr("Fit column to contents"); onTriggered: root.fitColumn(columnMenu.column) }
        AppMenuItem { text: qsTr("Fit all columns"); onTriggered: root.fitAllColumns() }
        AppMenuItem { text: qsTr("Reset column widths"); onTriggered: root.resetColumnWidths() }
        AppMenuItem { text: qsTr("Reset column order"); onTriggered: root.resetColumnOrder() }
        AppSeparator {}
        Instantiator {
            model: root.allColumns.length
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
        objectName: "trackMenu"
        // One menu for a right click, the Menu key / Shift+F10 and the toolbar's "More actions", built from the
        // toolbar's own actions so labels, keys and enabled states match. Store and SoundCloud entries are hidden in
        // local views and local-file entries in online views; within a view an entry that does not apply is disabled.
        readonly property bool local: !!desktop.view.local
        readonly property int selected: desktop.model.counts.selected
        readonly property string busyReason: root.hasSelection && desktop.busy ? qsTr("wait for the current task") : ""
        // An in-place edit that the opening click ended leaves nothing focused; the table takes the keyboard back.
        onClosed: if (!root.activeFocusItem || root.activeFocusItem === root.contentItem) table.forceActiveFocus()
        AppMenuItem { objectName: "play"; text: qsTr("Play"); keys: "Enter"; enabled: root.hasSelection; onTriggered: desktop.action("play") }
        AppMenuItem { objectName: "open"; action: openAction; visible: !contextMenu.local; reason: contextMenu.busyReason }
        AppSeparator {}
        AppMenuItem { objectName: "download"; action: downloadAction; visible: !contextMenu.local; reason: contextMenu.busyReason }
        AppMenuItem { objectName: "owned"; action: ownedAction; visible: !contextMenu.local }
        AppMenuItem { objectName: "skip"; action: skipAction }
        AppMenuItem { objectName: "reset"; action: resetAction }
        AppSeparator {}
        AppMenuItem { objectName: "copy"; text: qsTr("Copy artist and title"); keys: "Ctrl+C"; enabled: root.hasSelection; onTriggered: root.copied(desktop.copy()) }
        AppMenuItem { objectName: "copyLink"; text: qsTr("Copy link"); visible: !contextMenu.local; enabled: root.hasSelection; onTriggered: root.copied(desktop.copyLinks()) }
        AppSeparator {}
        AppMenuItem { objectName: "selectAll"; action: selectAllAction; enabled: root.hasRows }
        AppMenuItem { objectName: "invert"; text: qsTr("Invert selection"); enabled: root.hasRows; onTriggered: desktop.model.invertSelection() }
        AppSeparator { visible: contextMenu.local }
        AppMenuItem {
            objectName: "reveal"; text: qsTr("Reveal in file manager"); visible: contextMenu.local
            enabled: contextMenu.selected === 1; reason: contextMenu.selected > 1 ? qsTr("select one track") : ""
            onTriggered: desktop.reveal()
        }
        AppMenuItem { objectName: "edit"; action: editAction; visible: contextMenu.local; reason: contextMenu.selected > 1 ? qsTr("select one track") : "" }
        AppMenuItem { objectName: "analyze"; action: analyzeAction; visible: contextMenu.local; reason: contextMenu.busyReason }
        AppMenuItem {
            objectName: "export"; visible: contextMenu.local; enabled: root.hasSelection && !desktop.busy; reason: contextMenu.busyReason
            text: contextMenu.selected > 1 ? qsTr("Export %n tracks…", "", contextMenu.selected) : qsTr("Export audio…")
            onTriggered: desktop.action("export")
        }
        AppSeparator { visible: !contextMenu.local }
        AppMenuItem { objectName: "cart"; action: cartAction; visible: !contextMenu.local; reason: contextMenu.busyReason }
        AppSeparator {}
        AppMenuItem {
            objectName: "remove"; action: removeAction; visible: !contextMenu.local; destructive: true
            text: contextMenu.selected > 1 ? qsTr("Remove %n from playlist…", "", contextMenu.selected) : removeAction.text
        }
        AppMenuItem {
            objectName: "deleteFiles"; visible: contextMenu.local; destructive: true
            enabled: root.hasSelection && !desktop.busy; reason: contextMenu.busyReason
            text: contextMenu.selected > 1 ? qsTr("Delete %n files…", "", contextMenu.selected) : qsTr("Delete files…")
            onTriggered: desktop.action("delete_files")
        }
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
        objectName: "questionDialog"
        property string ident: ""
        property string body: ""
        property string error: ""
        property string okText: ""
        property string cancelText: ""
        property bool info: false
        property var fields: []
        property var values: ({})
        property var collapsed: ({})
        // A `buttons` field puts one footer button per option in place of OK.
        readonly property var choiceField: fields.find(f => f.kind === "buttons") ?? null
        function fieldSection(index) {
            for (let i = index; i >= 0; i--) if (fields[i].kind === "section") return fields[i].name
            return ""
        }
        // Wider when its footer buttons need it, so none is pushed outside.
        anchors.centerIn: parent; width: Math.min(Math.max(650, footer.implicitWidth), root.width - 50)
        height: Math.min(root.height - 50, implicitHeight)
        standardButtons: info ? Dialog.Ok : choiceField ? Dialog.Cancel : Dialog.Ok | Dialog.Cancel
        onOkTextChanged: { let ok = standardButton(Dialog.Ok); if (ok) ok.text = okText || qsTr("OK") }
        onCancelTextChanged: { let cancel = standardButton(Dialog.Cancel); if (cancel) cancel.text = cancelText || qsTr("Cancel") }
        onAccepted: desktop.answer(ident, values, true)
        onRejected: desktop.answer(ident, {}, false)
        onClosed: Qt.callLater(root.nextQuestion)
        function dressButtons() {
            let ok = standardButton(Dialog.Ok)
            if (ok) { ok.text = okText || qsTr("OK"); ok.highlighted = true; if (fields.length === 0) ok.forceActiveFocus() }
            let cancel = standardButton(Dialog.Cancel)
            if (cancel) cancel.text = cancelText || qsTr("Cancel")
        }
        // Again once settled: a change of standardButtons recreates the buttons after this point.
        onOpened: { dressButtons(); Qt.callLater(dressButtons) }
        footer: DialogButtonBox {
            visible: count > 0
            alignment: Qt.AlignRight; spacing: 8
            padding: 16; topPadding: 4
            background: null
            delegate: Button { implicitWidth: Math.max(96, implicitContentWidth + 32) }
            Repeater {
                model: dialog.choiceField ? dialog.choiceField.options : []
                Button {
                    required property var modelData
                    objectName: "choice-" + modelData[0]
                    readonly property bool preferred: modelData[0] === dialog.choiceField?.value
                    text: modelData[1]; highlighted: preferred; font.bold: preferred
                    implicitWidth: Math.max(96, implicitContentWidth + 32)
                    DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
                    Accessible.name: text
                    onClicked: { dialog.values[dialog.choiceField.name] = modelData[0]; dialog.accept() }
                }
            }
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
                        visible: modelData.kind !== "buttons" && (modelData.kind === "section" || !dialog.collapsed[dialog.fieldSection(index)])
                        // A section header; one that starts folded can be opened and folded again.
                        Label {
                            visible: fieldItem.modelData.kind === "section" && fieldItem.modelData.collapsed !== true && text.length > 0
                            Layout.fillWidth: true; Layout.topMargin: fieldItem.index > 0 ? 8 : 0
                            text: fieldItem.modelData.label; textFormat: Text.PlainText; font.bold: true; color: root.fg
                            Accessible.role: Accessible.Heading
                        }
                        Button {
                            objectName: "section-" + fieldItem.modelData.name
                            visible: fieldItem.modelData.kind === "section" && fieldItem.modelData.collapsed === true
                            Layout.topMargin: fieldItem.index > 0 ? 8 : 0
                            flat: true; font.bold: true; leftPadding: 0
                            text: (dialog.collapsed[fieldItem.modelData.name] ? "▸ " : "▾ ") + fieldItem.modelData.label
                            Accessible.name: fieldItem.modelData.label
                            onClicked: {
                                let folded = Object.assign({}, dialog.collapsed)
                                folded[fieldItem.modelData.name] = !folded[fieldItem.modelData.name]
                                dialog.collapsed = folded
                            }
                        }
                        Label { text: modelData.label; textFormat: Text.PlainText; color: root.muted; visible: text.length > 0 && ["bool", "section", "buttons"].indexOf(modelData.kind) < 0 }
                        RowLayout {
                            Layout.fillWidth: true
                            visible: ["text", "folder", "file", "savefile", "image", "number"].indexOf(fieldItem.modelData.kind) >= 0
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
                                visible: ["folder", "file", "savefile", "image"].indexOf(fieldItem.modelData.kind) >= 0
                                onClicked: {
                                    if (fieldItem.modelData.kind === "folder") {
                                        fieldFolder.targetName = fieldItem.modelData.name
                                        fieldFolder.open()
                                    } else {
                                        fieldFile.targetName = fieldItem.modelData.name
                                        fieldFile.fileMode = fieldItem.modelData.kind === "savefile" ? FileDialog.SaveFile : FileDialog.OpenFile
                                        fieldFile.nameFilters = fieldItem.modelData.kind === "image" ? [qsTr("Images") + " (*.png *.jpg *.jpeg *.webp)", qsTr("All files") + " (*)"]
                                                                                                      : ["JSON (*.json)", qsTr("All files") + " (*)"]
                                        fieldFile.open()
                                    }
                                }
                            }
                        }
                        // Read-only text the person can select; a path or an address also gets a button that copies it.
                        RowLayout {
                            visible: ["note", "path", "copy"].indexOf(fieldItem.modelData.kind) >= 0
                            Layout.fillWidth: true
                            TextEdit {
                                Layout.fillWidth: true
                                text: String(fieldItem.modelData.value); textFormat: TextEdit.PlainText
                                readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                                color: root.fg; font: Qt.application.font
                                Accessible.name: fieldItem.modelData.label; Accessible.role: Accessible.StaticText
                            }
                            Button {
                                objectName: "copy-" + fieldItem.modelData.name
                                text: fieldItem.modelData.kind === "path" ? qsTr("Copy path") : qsTr("Copy")
                                visible: fieldItem.modelData.kind === "path" || fieldItem.modelData.kind === "copy"
                                onClicked: root.copied(desktop.copyText(String(fieldItem.modelData.value)))
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
                                    wrapMode: Text.Wrap
                                    // Logs too: "monospace" has no fallback for the emoji and scripts in track titles.
                                    font.family: Qt.application.font.family
                                    // Only the person's edits are answers, not the text set when the dialog opens.
                                    onTextChanged: if (!readOnly && activeFocus) dialog.values[fieldItem.modelData.name] = text
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
                        // Plain-language help under a field or a section header.
                        Label {
                            Layout.fillWidth: true; visible: text.length > 0
                            text: fieldItem.modelData.hint || ""; textFormat: Text.PlainText; wrapMode: Text.Wrap
                            color: root.muted; font.pixelSize: root.fontCaption
                        }
                    }
                }
            }
        }
    }
    DoubleValidator { id: numberValidator; bottom: 0; top: 999; notation: DoubleValidator.StandardNotation; locale: "C" }
}
