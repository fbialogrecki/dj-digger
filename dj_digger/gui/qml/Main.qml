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
    property color bg: dark ? "#141820" : "#f4f6fa"
    property color panel: dark ? "#1c2330" : "#ffffff"
    property color fg: dark ? "#e3e9f2" : "#1e293b"
    property color muted: dark ? "#9aabc0" : "#526174"
    property color alternate: dark ? "#232d3d" : "#e9eef5"
    property color hoverSurface: dark ? "#2b3a50" : "#dce7f2"
    property color pressedSurface: dark ? "#41536b" : "#c2d1e2"
    property color disabledFg: dark ? "#8c9bb0" : "#637187"
    property color selectionText: dark ? "#141820" : "#ffffff"
    property color accent: dark ? "#64c9cb" : "#16777b"
    property color success: dark ? "#7ed492" : "#1f7a3a"
    property color warning: dark ? "#e8c46a" : "#8a5a00"
    property color danger: dark ? "#f08a8a" : "#b3261e"
    property real sidebarWidth: 220
    property bool sidebarVisible: true
    property bool shuttingDown: false
    property bool isMuted: false
    property real volume: desktop.volume
    property var pendingQuestions: []
    property var messages: []
    property string errorMessage: ""
    readonly property bool pauseTarget: !!desktop.audio.playing && (!hasSelection || desktop.model.firstSelectedKey === desktop.audio.key)
    property var columnWidths: [85, 150, 280, 85, 50, 45, 50, 95, 50, 140]
    readonly property int titleColumn: 2
    function columnVisible(column) { return (column !== 4 && column !== 5) || !!desktop.view.local }
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
    palette.highlight: accent
    palette.highlightedText: selectionText
    // Basic controls use these roles for alternate rows, placeholders, arrows,
    // popup states and tooltips. Never inherit the host's opposite-theme colors.
    palette.alternateBase: alternate
    palette.placeholderText: muted
    palette.light: hoverSurface
    palette.midlight: hoverSurface
    palette.mid: pressedSurface
    palette.dark: muted
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
    function statusColor(status) {
        return status === "got" ? success : status === "skip" ? muted : status === "opened" ? warning : fg
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
        sidebarWidth = Math.max(160, Math.min(400, Number(s.sidebarWidth) || 220))
        if (s.sidebarVisible === false) sidebarVisible = false
        desktop.language(languageCode)
        table.forceActiveFocus()
    }
    onClosing: function(event) {
        event.accepted = false
        if (!shuttingDown) {
            shuttingDown = true
            for (let i = 0; i < 10; i++) if (i !== titleColumn && columnVisible(i)) columnWidths[i] = table.columnWidth(i)
            desktop.saveSettings({sidebarWidth: sidebar.width, sidebarVisible: sidebarVisible, width: width, height: height, language: languageCode,
                                  theme: themeChoice, columnWidths: columnWidths})
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
                            background: Rectangle { color: playlistItem.highlighted ? root.accent : playlistItem.hovered ? root.hoverSurface : "transparent" }
                            required property var modelData
                            width: ListView.view.width; height: 28
                            padding: 0; leftPadding: 10; rightPadding: 6
                            highlighted: modelData.source === desktop.view.source
                            contentItem: RowLayout {
                                spacing: 6
                                Label { text: modelData.source.startsWith("local-playlist:") ? "▣" : "☁"; color: playlistItem.highlighted ? root.selectionText : root.muted }
                                Label { Layout.fillWidth: true; text: modelData.title; textFormat: Text.PlainText; elide: Text.ElideRight; color: playlistItem.highlighted ? root.selectionText : root.fg }
                            }
                            onClicked: desktop.load(modelData.source)
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
                                        background: Rectangle { color: rootItem.highlighted ? root.accent : rootItem.hovered ? root.hoverSurface : "transparent" }
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
                                            background: Rectangle { color: directoryDelegate.highlighted ? root.accent : directoryDelegate.hovered ? root.hoverSurface : root.bg }
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
                Layout.fillWidth: true; Layout.preferredHeight: root.loaded ? 156 : 52
                color: root.panel; radius: 6
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 10
                    Canvas {
                        id: waveform; objectName: "waveform"
                        visible: root.loaded
                        Layout.fillWidth: true; Layout.fillHeight: true
                        property var samples: desktop.audio.waveform || []
                        property real position: desktop.audio.position || 0
                        property var levels: desktop.waveformLevels(samples, Math.max(1, Math.floor(width / 3)))
                        onLevelsChanged: requestPaint()
                        onHeightChanged: requestPaint()
                        onVisibleChanged: requestPaint()
                        Connections { target: root; function onDarkChanged() { waveform.requestPaint() } }
                        onPositionChanged: requestPaint()
                        onWidthChanged: requestPaint()
                        onPaint: {
                            let ctx = getContext("2d"); ctx.reset()
                            let duration = desktop.audio.duration || 1
                            let count = levels.length
                            for (let i = 0; i < count; i++) {
                                ctx.fillStyle = i / count < position / duration ? root.accent : root.muted
                                let h = Math.max(1, levels[i] * (height - 2))
                                ctx.fillRect(i * width / count, height - h, Math.max(1, width / count - 1), h)
                            }
                            ctx.fillStyle = root.accent
                            ctx.fillRect(Math.min(width - 1, position / duration * width), 0, 1, height)
                        }
                        MouseArea {
                            id: waveformMouse
                            anchors.fill: parent; hoverEnabled: true
                            onClicked: mouse => desktop.transport("seek", mouse.x / width * (desktop.audio.duration || 0))
                            onPositionChanged: mouse => { if (pressed) desktop.transport("seek", mouse.x / width * (desktop.audio.duration || 0)) }
                            ToolTip.visible: containsMouse; ToolTip.delay: 300
                            ToolTip.text: root.clock(mouseX / width * (desktop.audio.duration || 0))
                        }
                        Accessible.role: Accessible.Slider
                        Accessible.name: qsTr("Waveform")
                    }
                    RowLayout {
                        Button { text: qsTr("Previous track"); enabled: root.loaded; display: AbstractButton.IconOnly; implicitWidth: 40; icon.source: "icons/previous.svg"; icon.color: palette.buttonText; Accessible.name: text; ToolTip.visible: hovered; ToolTip.text: text + " (P)"; onClicked: desktop.step(-1) }
                        Button {
                            objectName: "playPause"
                            implicitWidth: 40
                            enabled: root.loaded || root.hasSelection
                            text: root.pauseTarget ? qsTr("Pause") : qsTr("Play")
                            display: AbstractButton.IconOnly
                            icon.source: root.pauseTarget ? "icons/pause.svg" : "icons/play.svg"
                            icon.color: palette.buttonText; icon.width: 20; icon.height: 20
                            Accessible.name: text
                            ToolTip.visible: hovered; ToolTip.text: text + " (Space)"
                            onClicked: root.playSelected()
                        }
                        Button { text: qsTr("Next track"); enabled: root.loaded; display: AbstractButton.IconOnly; implicitWidth: 40; icon.source: "icons/next.svg"; icon.color: palette.buttonText; Accessible.name: text; ToolTip.visible: hovered; ToolTip.text: text + " (N)"; onClicked: desktop.step(1) }
                        Button { text: qsTr("Stop"); enabled: root.loaded; display: AbstractButton.IconOnly; implicitWidth: 40; icon.source: "icons/stop.svg"; icon.color: palette.buttonText; Accessible.name: text; ToolTip.visible: hovered; ToolTip.text: text + " (Ctrl+W)"; onClicked: desktop.transport("stop", 0) }
                        Label { visible: root.loaded; text: root.clock(desktop.audio.position) + " / " + root.clock(desktop.audio.duration); color: root.muted }
                        Label { visible: root.loaded; text: desktop.audio.title || ""; textFormat: Text.PlainText; elide: Text.ElideRight; Layout.fillWidth: true; Layout.minimumWidth: 0; font.bold: true }
                        Label { visible: !root.loaded; text: root.hasSelection ? qsTr("Press Space to play the selected track") : qsTr("Select a track to play"); color: root.muted; elide: Text.ElideRight; Layout.fillWidth: true; Layout.minimumWidth: 0 }
                        ToolButton {
                            text: root.isMuted ? qsTr("Unmute") : qsTr("Mute")
                            display: AbstractButton.TextOnly
                            checkable: true; checked: root.isMuted
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
                RowLayout {
                    TextField {
                        id: search; objectName: "trackSearch"; placeholderText: qsTr("Search artist, title, genre, tag or label  ( / )"); Layout.fillWidth: true; Layout.minimumWidth: 0
                        onTextChanged: filterTimer.restart()
                        Keys.onReturnPressed: table.forceActiveFocus()
                        Keys.onDownPressed: table.forceActiveFocus()
                        Accessible.name: qsTr("Search tracks")
                    }
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
                            background: Rectangle { color: storeOption.highlighted ? root.accent : root.panel }
                        }
                    }
                    CheckBox { id: hide; text: qsTr("Hide handled"); onToggled: filterTimer.restart(); ToolTip.visible: hovered; ToolTip.text: qsTr("Hide owned and skipped tracks (H)") }
                }
                Timer { id: filterTimer; interval: 120; onTriggered: desktop.model.filter(search.text, store.currentIndex > 0 && store.stores[store.currentIndex - 1] ? store.stores[store.currentIndex - 1].name : "", hide.checked) }
                HorizontalHeaderView {
                    id: header; syncView: table; Layout.fillWidth: true; clip: true
                    resizableColumns: true
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
                        TapHandler { onTapped: desktop.model.sortBy(index) }
                    }
                }
                Item {
                Layout.fillWidth: true; Layout.fillHeight: true
                TableView {
                    id: table; property int keyboardRow: 0
                    anchors.fill: parent
                    clip: true; model: desktop.model; reuseItems: true
                    // Title takes the remaining width so status and stores stay on screen.
                    // BPM and key only mean something for local files; SoundCloud rows never carry them.
                    columnWidthProvider: function(column) {
                        if (!root.columnVisible(column)) return 0
                        if (column !== root.titleColumn) return root.columnWidths[column]
                        let used = 0
                        for (let i = 0; i < 10; i++) if (i !== root.titleColumn && root.columnVisible(i)) used += root.columnWidths[i]
                        return Math.max(150, width - used - 12)
                    }
                    property bool localView: !!desktop.view.local
                    onLocalViewChanged: forceLayout()
                    onWidthChanged: forceLayout()
                    resizableColumns: true
                    Connections { target: desktop.model; function onModelReset() { table.keyboardRow = 0 } }
                    delegate: Rectangle {
                        id: cell
                        required property string display
                        required property bool chosen
                        required property string status
                        required property real progress
                        required property bool local
                        required property string trackKey
                        required property int row
                        required property int column
                        readonly property bool playing: desktop.audio.key === trackKey
                        implicitHeight: 34; implicitWidth: 100
                        color: chosen ? root.accent : (row % 2 ? root.panel : root.bg)
                        Rectangle {
                            visible: cell.progress >= 0
                            // Each cell paints its slice of one continuous row-wide fill.
                            width: Math.max(0, Math.min(cell.width,
                                table.contentWidth * Math.max(0, Math.min(1, cell.progress)) - cell.x))
                            height: parent.height
                            color: cell.chosen ? root.fg : root.accent
                            opacity: cell.chosen ? 0.16 : root.dark ? 0.28 : 0.20
                        }
                        Rectangle { visible: row === table.keyboardRow && table.activeFocus; anchors.fill: parent; color: "transparent"; border.color: root.accent; border.width: 1 }
                        Label {
                            visible: cell.column !== 9
                            anchors.fill: parent; anchors.margins: 7; textFormat: Text.PlainText; elide: Text.ElideRight
                            horizontalAlignment: [4, 6, 8].indexOf(cell.column) >= 0 ? Text.AlignRight : Text.AlignLeft
                            font.bold: cell.column === root.titleColumn && cell.playing
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
                                    color: cell.chosen ? root.selectionText : root.alternate
                                    Label { id: badge; anchors.centerIn: parent; text: parent.modelData; font.pixelSize: 12; color: cell.chosen ? root.accent : root.storeColor(parent.modelData) }
                                }
                            }
                        }
                        MouseArea {
                            anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: function(mouse) {
                                table.forceActiveFocus()
                                table.keyboardRow = row
                                if (mouse.button !== Qt.RightButton || !chosen) desktop.model.select(row, (mouse.modifiers & Qt.ControlModifier) !== 0, (mouse.modifiers & Qt.ShiftModifier) !== 0)
                                if (mouse.button === Qt.RightButton) contextMenu.popup()
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
                // Actions for the selection appear only while something is selected;
                // the rest of the time the table keeps the space.
                Rectangle {
                    visible: root.hasSelection
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    implicitHeight: 40; color: root.panel
                    RowLayout {
                        id: trackActions
                        objectName: "trackActions"
                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 4; spacing: 2
                        // Below this width the buttons keep their icons and tooltips only.
                        readonly property bool compact: width < 640
                        component ActionButton: FlatButton {
                            display: trackActions.compact ? AbstractButton.IconOnly : AbstractButton.TextBesideIcon
                            icon.width: 16; icon.height: 16
                            implicitHeight: 32; padding: 6; spacing: 6
                        }
                        Label { text: qsTr("%1 selected").arg(desktop.model.counts.selected); font.bold: true; color: root.fg; Layout.rightMargin: 8 }
                        ActionButton { text: qsTr("Open links"); keys: "O"; icon.source: "icons/link.svg"; onClicked: desktop.action("open") }
                        ActionButton { text: qsTr("Download"); keys: "D"; icon.source: "icons/download.svg"; enabled: !desktop.busy; onClicked: desktop.action("download") }
                        ActionButton { text: qsTr("Mark owned"); keys: "G"; icon.source: "icons/owned.svg"; onClicked: desktop.mark("got") }
                        ActionButton { text: qsTr("Skip"); keys: "K"; icon.source: "icons/skip.svg"; onClicked: desktop.mark("skip") }
                        ActionButton { text: qsTr("Analyze BPM / key"); icon.source: "icons/analyze.svg"; visible: !!desktop.view.local; enabled: !desktop.busy; onClicked: desktop.action("analyze") }
                        Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
                        ActionButton {
                            id: moreActions
                            text: qsTr("More actions"); icon.source: "icons/more.svg"; display: AbstractButton.IconOnly
                            onClicked: contextMenu.popup(moreActions, 0, -contextMenu.height)
                        }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    Layout.leftMargin: 10; Layout.rightMargin: 6; Layout.topMargin: 2; Layout.bottomMargin: 2
                    Label {
                        // Counts keep their width; the message beside them gives way first.
                        Layout.maximumWidth: parent.width * 0.7; Layout.minimumWidth: 0
                        color: root.muted; elide: Text.ElideRight
                        property var c: desktop.model.counts
                        text: root.hasRows || c.total > 0 ? qsTr("%1 / %2 tracks · owned %3 · skipped %4").arg(c.visible).arg(c.total).arg(c.got).arg(c.skipped) : ""
                    }
                    BusyIndicator { running: desktop.busy || root.shuttingDown || !desktop.ready; visible: running; implicitHeight: 24; implicitWidth: 24 }
                    FlatButton { text: qsTr("Cancel"); display: AbstractButton.TextOnly; visible: desktop.busy; implicitHeight: 26; onClicked: desktop.action("cancel") }
                    Label {
                        Layout.fillWidth: true; Layout.minimumWidth: 0; textFormat: Text.PlainText; elide: Text.ElideRight
                        text: root.shuttingDown ? qsTr("Closing…") : !desktop.ready ? qsTr("Loading library…") : desktop.level === "error" ? "" : desktop.message
                        color: desktop.level === "error" ? root.danger : root.muted
                        font.bold: desktop.level === "error"
                        ToolTip.visible: hovered && text.length > 0; ToolTip.text: text
                        HoverHandler { id: messageHover }
                        property bool hovered: messageHover.hovered
                        TapHandler { onTapped: messageLog.open() }
                        Accessible.name: text
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
    }
    FolderDialog { id: folderDialog; title: qsTr("Add folder"); onAccepted: desktop.addFolder(selectedFolder.toString()) }
    AppMenu {
        id: playlistMenu
        property string source
        AppMenuItem { text: qsTr("Open"); onTriggered: desktop.load(playlistMenu.source) }
        AppMenuItem { text: qsTr("Delete playlist…"); keys: "Shift+X"; onTriggered: desktop.deletePlaylist(playlistMenu.source) }
    }
    AppMenu {
        id: contextMenu
        AppMenuItem { text: qsTr("Play / pause"); keys: "Space"; onTriggered: desktop.action("play") }
        AppMenuItem { text: qsTr("Open links"); keys: "O"; onTriggered: desktop.action("open") }
        AppMenuItem { text: qsTr("Download"); keys: "D"; enabled: !desktop.busy; onTriggered: desktop.action("download") }
        MenuSeparator {}
        AppMenuItem { text: qsTr("Mark owned"); keys: "G"; onTriggered: desktop.mark("got") }
        AppMenuItem { text: qsTr("Skip"); keys: "K"; onTriggered: desktop.mark("skip") }
        AppMenuItem { text: qsTr("Reset status"); keys: "U"; onTriggered: desktop.mark("new") }
        MenuSeparator {}
        AppMenuItem { text: qsTr("Copy artist and title"); keys: "Ctrl+C"; onTriggered: desktop.copy() }
        AppMenuItem { text: qsTr("Analyze BPM / key"); enabled: !desktop.busy; onTriggered: desktop.action("analyze") }
        AppMenuItem { text: qsTr("Edit BPM / key…"); keys: "E"; enabled: desktop.model.counts.selected === 1; onTriggered: desktop.action("edit") }
        AppMenuItem { text: qsTr("Export audio…"); enabled: !desktop.busy; onTriggered: desktop.action("export") }
        AppMenuItem { text: qsTr("Prepare cart / Beatport playlist"); keys: "C"; enabled: !desktop.busy; onTriggered: desktop.action("cart") }
        MenuSeparator {}
        AppMenuItem { text: qsTr("Remove from playlist…"); keys: "X"; enabled: root.remoteView; onTriggered: desktop.action("remove") }
        AppMenuItem { text: qsTr("Delete files…"); enabled: !desktop.busy; onTriggered: desktop.action("delete_files") }
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
        nameFilters: ["JSON / CSV (*.json *.csv)", "All files (*)"]
        onAccepted: {
            let updated = Object.assign({}, dialog.values)
            updated[targetName] = desktop.localPath(selectedFile.toString())
            dialog.values = updated
        }
    }
    Dialog {
        id: helpDialog
        title: qsTr("Keyboard shortcuts")
        anchors.centerIn: parent; width: Math.min(560, root.width - 50); height: Math.min(root.height - 50, implicitHeight)
        modal: true; standardButtons: Dialog.Close
        contentItem: ScrollView {
            clip: true; implicitHeight: helpText.implicitHeight + 8
            TextArea { id: helpText; readOnly: true; textFormat: Text.PlainText; font.family: "monospace"; background: null }
        }
    }
    Dialog {
        id: messageLog
        title: qsTr("Messages")
        anchors.centerIn: parent; width: Math.min(720, root.width - 50); height: Math.min(root.height - 50, implicitHeight)
        modal: true; standardButtons: Dialog.Close
        contentItem: ScrollView {
            clip: true; implicitHeight: Math.max(120, logText.implicitHeight + 8)
            TextArea { id: logText; readOnly: true; textFormat: Text.PlainText; selectByMouse: true; wrapMode: Text.Wrap; background: null
                       text: root.messages.length ? root.messages.join("\n") : qsTr("No messages yet.") }
        }
    }
    Dialog {
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
        modal: true; standardButtons: info ? Dialog.Ok : Dialog.Ok | Dialog.Cancel
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
