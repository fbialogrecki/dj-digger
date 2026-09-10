"""Keep Qt Quick modules; the default hook also bundles unrelated WebEngine QML."""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()


def used(entry):
    destination = entry[1].replace('\\', '/')
    module = destination.split('/qml/', 1)[-1]
    return (module == 'QtCore' or module == 'QtQml' or module.startswith('QtQml/')
            or module == 'QtQuick' or module.startswith('QtQuick/')
            or module in {'Qt/labs/platform', 'Qt/labs/folderlistmodel'}) and not any(
                part in module for part in ('Scene2D', 'Scene3D'))


binaries += [entry for entry in qml_binaries if used(entry)]
datas += [entry for entry in qml_datas if used(entry)]
