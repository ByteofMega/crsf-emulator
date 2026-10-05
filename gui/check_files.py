"""check_files.py - проверка, что в каждом файле проекта лежит то, что нужно.
Запуск из папки с main.py:   python check_files.py
(не импортирует PyQt: только читает файлы)"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPECT = {
    "main.py": ["class MainWindow"],
    "ws_client.py": ["class CrsfWsClient"],
    "cli_ws.py": ["class FcCliClient", "def request_abort"],
    "msp_codes.py": ["def decode", "READ_CODES"],
    "msp_panel.py": ["class MspPanel"],
    "settings_panel.py": ["class FcSettingsPanel", "def filter_candidates"],
    "position_estimator.py": ["class DeadReckoning", "def calibrate_kd"],
    "route_planner.py": ["class WaypointNavigator", "def estimate_energy_usage"],
    "map_view.py": ["class MapView"],
    "map.html": ["ymaps"],
    "channel_widgets.py": ["class ChannelPanel"],
    "telemetry_panel.py": ["class TelemetryPanel"],
    "config.py": ["STYLESHEET", "CHANNEL_NAMES"],
}


def defined_names(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names.add("class " + node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add("def " + node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


bad = 0
for fname, need in EXPECT.items():
    path = os.path.join(HERE, fname)
    if not os.path.exists(path):
        print(f"НЕТ ФАЙЛА   {fname}")
        bad += 1
        continue
    text = open(path, encoding="utf-8", errors="replace").read()
    if not text.strip():
        print(f"ПУСТОЙ      {fname}")
        bad += 1
        continue
    if fname.endswith(".py"):
        try:
            names = defined_names(ast.parse(text))
        except SyntaxError as e:
            print(f"СИНТАКСИС   {fname}: строка {e.lineno}: {e.msg}")
            bad += 1
            continue
        missing = [n for n in need if n not in names]
        if missing:
            first = text.strip().splitlines()[0][:70]
            print(f"НЕ ТОТ ФАЙЛ {fname}: нет {missing}; первая строка: {first!r}")
            bad += 1
            continue
    else:
        missing = [n for n in need if n not in text]
        if missing:
            print(f"НЕ ТОТ ФАЙЛ {fname}: нет {missing}")
            bad += 1
            continue
    print(f"ok          {fname}")
print("\nВсе файлы на месте" if not bad else f"\nПроблемных файлов: {bad}")
sys.exit(1 if bad else 0)
